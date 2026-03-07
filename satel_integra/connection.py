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
        self._transport: SatelBaseTransport = (
            SatelEncryptedTransport(host, port, integration_key)
            if integration_key
            else SatelPlainTransport(host, port)
        )

        self._closed = False
        self._connection_lock = asyncio.Lock()  # Prevent concurrent connect/close
        self._reconnected_event = (
            asyncio.Event()
        )  # Signals when connection is re-established
        self._had_connection = False

    @property
    def connected(self) -> bool:
        """Return True if connected to the panel."""
        return self._transport.connected

    @property
    def closed(self) -> bool:
        """Return True if the connection is closed."""
        return self._closed

    async def _connect(self, check_busy: bool = True) -> bool:
        """Establish TCP connection. Must be called with _connection_lock held."""
        if self.closed:
            _LOGGER.debug("Connection is closed, skipping connection")
            return False

        if self.connected:
            _LOGGER.debug("Already connected, skipping connection")
            return True

        _LOGGER.debug("Connecting to Satel Integra at %s:%s...", self._host, self._port)

        await self._transport.connect()
        if not await self._transport.wait_connected():
            _LOGGER.warning("Unable to establish TCP connection.")
            return False

        if check_busy:
            _LOGGER.debug(
                "TCP connection established, verifying panel responsiveness..."
            )
            if not await self._transport.check_connection():
                _LOGGER.warning("Panel not responsive or busy.")
                await self._transport.close()
                return False
        else:
            _LOGGER.debug(
                "TCP connection established, skipping busy/panel responsiveness check."
            )

        _LOGGER.info("Connected to Satel Integra.")
        # If we've had a successful connection before, this is a
        # reconnection — signal any waiters. Otherwise mark that we've
        # now had a connection so future connects can be treated as
        # reconnections.
        if self._had_connection:
            self._reconnected_event.set()

        self._had_connection = True
        return True

    async def connect(self, check_busy: bool = True) -> bool:
        """Establish TCP connection with a single attempt (no retries).

        Acquires lock internally. Suitable for setup validation where a single
        connection failure should not trigger automatic retries.
        """
        async with self._connection_lock:
            if self.closed:
                return False
            if self.connected:
                return True
            return await self._connect(check_busy=check_busy)

    async def read_frame(self) -> bytes | None:
        """Read a raw frame from the panel."""
        return await self._transport.read_frame()

    async def send_frame(self, frame: bytes) -> bool:
        """Send a raw frame to the panel."""
        return await self._transport.send_frame(frame)

    async def ensure_connected(self) -> bool:
        """Reconnect automatically if disconnected."""
        if self.connected:
            return True

        if self.closed:
            return False

        async with self._connection_lock:
            # Double-check after acquiring lock
            if self.connected:
                return True

            _LOGGER.debug("Not connected, attempting reconnection...")
            success = await self._connect()
            if not success:
                _LOGGER.warning(
                    "Connection failed, retrying in %ss...", self._reconnection_timeout
                )
                await asyncio.sleep(self._reconnection_timeout)

            return self.connected

    async def close(self) -> None:
        """Close the connection gracefully and clean up."""
        async with self._connection_lock:
            if self.closed:
                return  # already closed, avoid duplicate calls

            _LOGGER.debug("Closing connection...")
            await self._transport.close()
            self._closed = True
            _LOGGER.info("Connection closed cleanly.")

    async def wait_reconnected(self) -> bool:
        """Wait for connection to be re-established after being lost.

        Blocks indefinitely until a reconnection occurs.
        Returns False if the connection is closed.
        """
        if self.closed:
            return False

        self._reconnected_event.clear()
        await self._reconnected_event.wait()
        return True
