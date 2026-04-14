"""Connection management for Satel Integra panel."""

import asyncio
import inspect
import logging

from satel_integra.const import FRAME_END, ConnectionStateCallback
from satel_integra.encryption import EncryptedCommunicationHandler
from satel_integra.exceptions import SatelConnectFailedError

_LOGGER = logging.getLogger(__name__)


class SatelBaseTransport:
    """Base class for Satel Integra transport."""

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

        self._connection_event = asyncio.Event()
        self._connection_state_callbacks: list[ConnectionStateCallback] = []

    @property
    def connected(self) -> bool:
        """Return True if connected to the panel."""
        return self._reader is not None and self._writer is not None

    def add_connection_state_callback(self, callback: ConnectionStateCallback) -> None:
        """Add a callback to be called when transport connection status changes."""
        self._connection_state_callbacks.append(callback)

    async def _set_connection_state(self, connected: bool) -> None:
        """Set the connection event and notify callbacks."""
        was_connected = self._connection_event.is_set()
        if connected == was_connected:
            return

        if connected:
            self._connection_event.set()
        else:
            self._connection_event.clear()

        await self._notify_connection_state_changed()

    async def _notify_connection_state_changed(self) -> None:
        """Invoke callback when connection state changes."""
        for callback in self._connection_state_callbacks:
            try:
                result = callback()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                _LOGGER.exception("Error in connection state callback: %s", exc)

    async def _reset_connection(self) -> None:
        """Reset transport connection handles and clear connection event."""
        self._reader = None
        self._writer = None
        await self._set_connection_state(False)

    async def connect(self) -> None:
        """Establish TCP connection."""

        try:
            self._reader, self._writer = await asyncio.open_connection(
                self._host, self._port
            )
            _LOGGER.debug("TCP connection established to %s:%s", self._host, self._port)
            await self._set_connection_state(True)

        except Exception as exc:
            _LOGGER.debug(
                "TCP connection to %s:%s failed: %s", self._host, self._port, exc
            )
            await self.close()
            raise SatelConnectFailedError(
                f"Unable to establish TCP connection to {self._host}:{self._port}"
            ) from exc

    async def read_initial_data(self) -> bytes | None:
        """Read raw data available immediately after TCP connect."""
        if not self._reader:
            _LOGGER.debug("Cannot read initial data, not connected.")
            return None

        return await self._reader.read(-1)

    async def read_frame(self) -> bytes | None:
        """Template method for reading a frame from the panel."""
        if not self._reader:
            _LOGGER.debug("Cannot read, not connected.")
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
                _LOGGER.debug("Read failed, no frame end marker found.")
        except asyncio.IncompleteReadError:
            # Incomplete read due to connection closing
            pass
        except Exception as e:
            _LOGGER.debug("Read failed: %s", e)

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
            _LOGGER.debug("Cannot write, not connected.")
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
            _LOGGER.debug("Write failed: %s", e)
            await self.close()

            raise

    def _prepare_frame(self, frame: bytes) -> bytes | None:
        """Prepare frame for writing (e.g., encrypt). Override in subclass if needed."""
        return frame

    async def close(self) -> None:
        """Close the connection gracefully and clean up."""
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception as e:
                _LOGGER.debug("Exception during close: %s", e)

        await self._reset_connection()


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
            _LOGGER.debug("Cannot read, not connected.")
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
