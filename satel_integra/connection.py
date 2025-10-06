"""Connection management for Satel Integra panel."""

import asyncio
import logging

from satel_integra.const import FRAME_END

_LOGGER = logging.getLogger(__name__)


class SatelConnection:
    """Manages TCP connection and I/O for the Satel Integra panel."""

    def __init__(self, host: str, port: int, reconnection_timeout: int = 15) -> None:
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self.closed = False
        self._reconnection_timeout = reconnection_timeout

    @property
    def connected(self) -> bool:
        """Return True if connected to the panel."""
        return self._reader is not None and self._writer is not None

    async def connect(self) -> bool:
        """Establish TCP connection."""
        if self.closed:
            _LOGGER.debug("Connection is closed, skipping connection")
            return False

        _LOGGER.debug("Connecting to Satel Integra at %s:%s...", self._host, self._port)
        try:
            self._reader, self._writer = await asyncio.open_connection(
                self._host, self._port
            )
        except Exception as e:
            _LOGGER.warning("Connection failed: %s", e)
            self._reader, self._writer = None, None
            return False
        else:
            _LOGGER.info("Connected to Satel Integra.")
            return True

    async def read_frame(self) -> bytes | None:
        """Read a raw frame from the panel."""
        if not self._reader:
            _LOGGER.warning("Cannot read, not connected.")
            return None

        try:
            frame = await self._reader.readuntil(FRAME_END)
        except Exception as e:
            _LOGGER.warning("Read failed: %s", e)
            self._reader = None
            self._writer = None
            return None
        else:
            _LOGGER.debug("Received raw frame: %s", frame.hex())
            return frame

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

    async def ensure_connected(self) -> bool:
        """Reconnect automatically if disconnected."""
        if self.connected:
            return True

        _LOGGER.debug("Not connected, attempting reconnection...")
        while not self.connected and not self.closed:
            success = await self.connect()
            if not success:
                _LOGGER.warning(
                    "Reconnect failed, retrying in %ss...", self._reconnection_timeout
                )
                await asyncio.sleep(self._reconnection_timeout)

        return self.connected

    async def close(self) -> None:
        """Close the connection gracefully and clean up."""
        if self.closed or not self.connected:
            return  # already closed, avoid duplicate calls

        self.closed = True
        _LOGGER.debug("Closing connection...")

        if self._writer and not self._writer.is_closing():
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception as e:
                _LOGGER.warning("Exception during close: %s", e)

        self._reader = None
        self._writer = None

        _LOGGER.info("Connection closed cleanly.")
