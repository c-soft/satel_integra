"""Connection management for Satel Integra panel."""

import asyncio
import logging

from satel_integra.const import FRAME_END
from satel_integra.encryption import EncryptedCommunicationHandler

_LOGGER = logging.getLogger(__name__)


class PlainConnection:
    """Plain data writer and reader."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    @property
    def connected(self) -> bool:
        """Return True if connected to the panel."""
        return self._reader is not None and self._writer is not None

    async def connect(self) -> bool:
        """Establish TCP connection."""
        try:
            self._reader, self._writer = await asyncio.open_connection(
                self._host, self._port
            )
            _LOGGER.debug("TCP connection established to %s:%s", self._host, self._port)
            return True
        except Exception as exc:
            _LOGGER.debug("TCP connection failed: %s", exc)
            self._reader = self._writer = None
            return False

    async def check_connection(self) -> bool:
        """Check if the connection is valid and the panel is responsive."""
        if not self._reader or not self._writer:
            _LOGGER.warning("Cannot check connection, not connected.")
            return False

        try:
            # Try reading to end of file
            data = await asyncio.wait_for(self._reader.read(-1), timeout=0.1)

            # Satel returns a string starting with "Busy" when another client is connected
            if b"Busy" in data:
                _LOGGER.warning("Panel reports busy (another client is connected).")
                await self.close()
                return False

            # We assume any other data is fine, but we log it for debugging reasons
            _LOGGER.debug("Received data after connect: %s", data)
        except asyncio.TimeoutError:
            # Timeout is fine, it means we can actually read data
            pass
        except Exception as exc:
            _LOGGER.debug("Connection check failed: %s", exc)
            await self.close()
            return False

        return True

    async def read_frame(self) -> bytes | None:
        """Read a raw frame from the panel."""
        if not self._reader:
            _LOGGER.warning("Cannot read, not connected.")
            return None

        try:
            frame = await self._reader.readuntil(FRAME_END)
        except asyncio.IncompleteReadError:
            _LOGGER.debug("Incomplete read due to connection closing")
        except Exception as e:
            _LOGGER.warning("Read failed: %s", e)
        else:
            _LOGGER.debug("Received raw frame: %s", frame.hex())
            return frame

        self._reader = None
        self._writer = None
        return None

    async def send_frame(self, frame: bytes) -> bool:
        """Send a raw frame to the panel."""
        if not self._writer:
            _LOGGER.warning("Cannot send, not connected.")
            return False

        try:
            self._writer.write(frame)
            await self._writer.drain()
        except Exception as e:
            _LOGGER.warning("Write failed: %s", e)
            self._reader = None
            self._writer = None
            return False
        else:
            _LOGGER.debug("Sent raw frame: %s", frame.hex())
            return True

    async def close(self) -> None:
        """Close the connection gracefully and clean up."""
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception as e:
                _LOGGER.warning("Exception during close: %s", e)

        self._reader = None
        self._writer = None


class EncryptedConnection(PlainConnection):
    """Encrypted data writer and reader."""

    def __init__(self, host: str, port: int, integration_key: str) -> None:
        self._integration_key = integration_key
        self._encryption_handler = EncryptedCommunicationHandler(self._integration_key)
        super().__init__(host, port)

    async def read_frame(self) -> bytes | None:
        """Read encrypted frame end decrypt it."""

        if not self._reader:
            _LOGGER.warning("Cannot read, not connected.")
            return None

        # first byte tells about data length
        data_len = ord(await self._reader.read(1))
        # read rest of data
        data = await self._reader.read(data_len)
        _LOGGER.debug("Encrypted frame: %s", data.hex())
        decrypted_frame = self._encryption_handler.extract_data_from_pdu(data)
        _LOGGER.debug("Decrypted frame: %s", decrypted_frame.hex())
        if FRAME_END in decrypted_frame:
            # there may be padding after the frame end marker, trim the padding
            decrypted_frame = decrypted_frame.split(FRAME_END)[0] + FRAME_END
        else:
            _LOGGER.warning("Read failed, received frame without frame end marker")
            self._reader = None
            self._writer = None
            return None
        return decrypted_frame

    async def send_frame(self, frame: bytes) -> bool:
        """Send a raw frame to the panel."""
        _LOGGER.debug("Frame before encryption: %s", frame.hex())
        encrypted_frame = self._encryption_handler.prepare_pdu(frame)
        encrypted_frame = (
            len(encrypted_frame)
        ).to_bytes() + encrypted_frame  # add PDU length at the beginning

        return await super().send_frame(encrypted_frame)


class SatelConnection:
    """Manages TCP connection and I/O for the Satel Integra panel."""

    def __init__(
        self,
        host: str,
        port: int,
        reconnection_timeout: int = 15,
        integration_key: str = "",
    ) -> None:
        self._host = host
        self._port = port
        self.closed = False
        self._reconnection_timeout = reconnection_timeout
        self._connection = (
            EncryptedConnection(host, port, integration_key)
            if integration_key
            else PlainConnection(host, port)
        )

    @property
    def connected(self) -> bool:
        """Return True if connected to the panel."""
        return self._connection.connected

    async def connect(self) -> bool:
        """Establish TCP connection."""
        if self.closed:
            _LOGGER.debug("Connection is closed, skipping connection")
            return False

        _LOGGER.debug("Connecting to Satel Integra at %s:%s...", self._host, self._port)

        if not await self._connection.connect():
            _LOGGER.warning("Unable to establish TCP connection.")
            return False

        _LOGGER.debug("TCP connection established, verifying panel responsiveness...")

        if not await self._connection.check_connection():
            _LOGGER.warning("Panel not responsive or busy.")
            await self._connection.close()
            return False

        else:
            _LOGGER.info("Connected to Satel Integra.")
            return True

    async def read_frame(self) -> bytes | None:
        """Read a raw frame from the panel."""
        return await self._connection.read_frame()

    async def send_frame(self, frame: bytes) -> bool:
        """Send a raw frame to the panel."""
        return await self._connection.send_frame(frame)

    async def ensure_connected(self) -> bool:
        """Reconnect automatically if disconnected."""
        if self.connected:
            return True

        _LOGGER.debug("Not connected, attempting reconnection...")
        while not self.connected and not self.closed:
            success = await self.connect()
            if not success:
                _LOGGER.warning(
                    "Connection failed, retrying in %ss...", self._reconnection_timeout
                )
                await asyncio.sleep(self._reconnection_timeout)

        return self.connected

    async def close(self) -> None:
        """Close the connection gracefully and clean up."""
        if self.closed or not self.connected:
            return  # already closed, avoid duplicate calls

        self.closed = True
        _LOGGER.debug("Closing connection...")
        await self._connection.close()
        _LOGGER.info("Connection closed cleanly.")
