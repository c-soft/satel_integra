"""Connection management for Satel Integra panel."""

import asyncio
import logging

from satel_integra.const import FRAME_END
from satel_integra.encryption import EncryptedCommunicationHandler

_LOGGER = logging.getLogger(__name__)


class SatelBaseTransport:
    """Base class for Satel Integra transport."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

        self._connection_event = asyncio.Event()

    @property
    def connected(self) -> bool:
        """Return True if connected to the panel."""
        return self._reader is not None and self._writer is not None

    async def connect(self) -> None:
        """Establish TCP connection."""
        try:
            self._reader, self._writer = await asyncio.open_connection(
                self._host, self._port
            )
            _LOGGER.debug("TCP connection established to %s:%s", self._host, self._port)
            self._connection_event.set()

        except Exception as exc:
            _LOGGER.debug("TCP connection failed: %s", exc)
            await self.close()

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
        """Template method for reading a frame from the panel."""
        if not self._reader:
            _LOGGER.warning("Cannot read, not connected.")
            return None

        try:
            raw_data = await self._read_from_transport()

            if raw_data is None:
                return None

            frame = self._process_frame(raw_data)

            if frame and FRAME_END in frame:
                frame = frame.split(FRAME_END)[0] + FRAME_END
                _LOGGER.debug("Received raw frame: %s", frame.hex())
                return frame
            else:
                _LOGGER.warning("Read failed, no frame end marker found.")
        except asyncio.IncompleteReadError:
            # Incomplete read due to connection closing
            pass
        except Exception as e:
            _LOGGER.warning("Read failed: %s", e)

        await self.close()
        return None

    async def _read_from_transport(self) -> bytes | None:
        """Read raw bytes from the transport. Implement in subclass."""
        raise NotImplementedError

    def _process_frame(self, raw_data: bytes) -> bytes | None:
        """Process the frame (e.g., decrypt). Override in subclass if needed."""
        return raw_data

    async def send_frame(self, frame: bytes) -> bool:
        """Template method for writing a frame to the panel."""
        if not self._writer:
            _LOGGER.warning("Cannot write, not connected.")
            return False

        try:
            data = self._prepare_frame(frame)
            if not data:
                raise ValueError("Frame preparation failed or returned empty data")

            self._writer.write(data)
            await self._writer.drain()

            _LOGGER.debug("Sent raw fame: %s", data.hex())
            return True

        except Exception as e:
            _LOGGER.warning("Write failed: %s", e)
            await self.close()
            return False

    def _prepare_frame(self, frame: bytes) -> bytes | None:
        """Prepare frame for writing (e.g., encrypt). Override in subclass if needed."""
        return frame

    async def close(self) -> None:
        """Close the connection gracefully and clean up."""
        self._connection_event.clear()

        if self._writer and not self._writer.is_closing():
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception as e:
                _LOGGER.warning("Exception during close: %s", e)

        self._reader = None
        self._writer = None

    async def wait_connected(self, timeout: float | None = None) -> bool:
        """Wait until connection is established."""
        try:
            await asyncio.wait_for(self._connection_event.wait(), timeout=timeout)
            return self.connected
        except asyncio.TimeoutError:
            return False


class SatelPlainTransport(SatelBaseTransport):
    async def _read_from_transport(self) -> bytes | None:
        """Read until frame end marker."""
        if self._reader is None:
            return None

        return await self._reader.readuntil(FRAME_END)


class SatelEncryptedTransport(SatelBaseTransport):
    """Encrypted data writer and reader."""

    def __init__(self, host: str, port: int, integration_key: str) -> None:
        self._integration_key = integration_key
        self._encryption_handler: EncryptedCommunicationHandler
        super().__init__(host, port)

    async def connect(self) -> None:
        self._encryption_handler = EncryptedCommunicationHandler(self._integration_key)
        await super().connect()

    async def _read_from_transport(self) -> bytes | None:
        """Read encrypted frame end decrypt it."""

        if not self._reader:
            _LOGGER.warning("Cannot read, not connected.")
            return None

        # first byte tells about data length
        data_len_bytes = await self._reader.read(1)

        if not data_len_bytes:
            await self.close()
            raise ValueError("No data length received, possibly wrong integration key")

        data_len = data_len_bytes[0]

        return await self._reader.read(data_len)

    def _process_frame(self, raw_data: bytes) -> bytes | None:
        _LOGGER.debug("Encrypted frame: %s", raw_data.hex())
        decrypted_frame = self._encryption_handler.extract_data_from_pdu(raw_data)
        _LOGGER.debug("Decrypted frame: %s", decrypted_frame.hex())
        return decrypted_frame

    def _prepare_frame(self, frame: bytes) -> bytes | None:
        """Send a raw frame to the panel."""
        _LOGGER.debug("Frame before encryption: %s", frame.hex())
        encrypted_frame = self._encryption_handler.prepare_pdu(frame)
        encrypted_frame = (
            len(encrypted_frame)
        ).to_bytes() + encrypted_frame  # add PDU length at the beginning

        return encrypted_frame
