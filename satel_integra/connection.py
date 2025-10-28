"""Connection management for Satel Integra panel."""

import asyncio
import logging

from satel_integra.transport import (
    SatelBaseTransport,
    SatelEncryptedTransport,
    SatelPlainTransport,
)

_LOGGER = logging.getLogger(__name__)


class SatelConnection:
    """Manages TCP connection and I/O for the Satel Integra panel."""

    def __init__(
        self,
        host: str,
        port: int,
        reconnection_timeout: int = 15,
        integration_key: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._reconnection_timeout = reconnection_timeout
        self._connection: SatelBaseTransport = (
            SatelEncryptedTransport(host, port, integration_key)
            if integration_key
            else SatelPlainTransport(host, port)
        )

    @property
    def connected(self) -> bool:
        """Return True if connected to the panel."""
        return self._connection.connected

    @property
    def closed(self) -> bool:
        """Return True if the connection is closed."""
        return self._connection.closed

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

        while not self.connected and not self.closed:
            _LOGGER.debug("Not connected, attempting reconnection...")
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

        _LOGGER.debug("Closing connection...")
        await self._connection.close()
        _LOGGER.info("Connection closed cleanly.")
