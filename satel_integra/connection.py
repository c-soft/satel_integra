"""Connection management for Satel Integra panel."""

import asyncio
import logging

from satel_integra.commands import SatelWriteCommand
from satel_integra.const import MESSAGE_RESPONSE_TIMEOUT
from satel_integra.exceptions import SatelConnectionStoppedError
from satel_integra.messages import SatelWriteMessage
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

        self._stopped = False
        self._stopped_event = asyncio.Event()
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
    def stopped(self) -> bool:
        """Return True if the connection is stopped."""
        return self._stopped

    def _assert_not_stopped(self) -> None:
        """Raise if the connection is in a terminal stopped state."""
        if self.stopped:
            raise SatelConnectionStoppedError("Connection is stopped")

    async def _connect(self, verify_connection: bool = True) -> bool:
        """Establish TCP connection. Must be called with _connection_lock held."""
        if self.stopped:
            _LOGGER.debug("Connection is closed, skipping connection")
            return False

        if self.connected:
            _LOGGER.debug("Already connected, skipping connection")
            return True

        _LOGGER.debug("Connecting to Satel Integra at %s:%s...", self._host, self._port)

        await self._transport.connect()
        if not await self._transport.wait_connected():
            _LOGGER.warning("Unable to establish TCP connection.")
            await self._close_locked()
            return False

        if verify_connection:
            _LOGGER.debug("TCP connection established, verifying panel responsiveness")
            if not await self._check_connection():
                _LOGGER.warning(
                    "Connected to the panel, but it is not ready for use. "
                    "Another client may already be connected, or the panel may "
                    "still be busy."
                )
                await self._close_locked()
                return False

            _LOGGER.debug("TCP connection established, verifying protocol round-trip")
            if not await self._verify_protocol():
                _LOGGER.warning(
                    "Connected to the panel, but startup validation failed. "
                    "Check that the integration key and encryption settings match "
                    "the panel configuration."
                )
                await self._close_locked()
                return False

        else:
            _LOGGER.debug(
                "TCP connection established, skipping connection health check."
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

    async def connect(self, verify_connection: bool = True) -> bool:
        """Establish TCP connection with a single attempt (no retries).

        Acquires lock internally. Suitable for setup validation where a single
        connection failure should not trigger automatic retries.
        """
        async with self._connection_lock:
            if self.stopped:
                return False
            if self.connected:
                return True
            return await self._connect(verify_connection=verify_connection)

    async def read_frame(self) -> bytes | None:
        """Read a raw frame from the panel."""
        return await self._transport.read_frame()

    async def send_frame(self, frame: bytes) -> bool:
        """Send a raw frame to the panel."""
        return await self._transport.send_frame(frame)

    async def ensure_connected(self) -> None:
        """Reconnect automatically until connected or terminally stopped."""
        while not self.connected:
            self._assert_not_stopped()

            async with self._connection_lock:
                # Double-check after acquiring lock
                if self.connected:
                    return

                self._assert_not_stopped()

                _LOGGER.debug("Not connected, attempting reconnection...")
                success = await self._connect()

            if success:
                return

            self._assert_not_stopped()
            _LOGGER.warning(
                "Connection failed, retrying in %ss...", self._reconnection_timeout
            )
            await asyncio.sleep(self._reconnection_timeout)

    async def _close_locked(self) -> None:
        """Close the connection while the connection lock is already held."""
        if self.stopped:
            return

        _LOGGER.debug("Closing connection...")
        await self._transport.close()
        self._stopped = True
        self._stopped_event.set()
        _LOGGER.info("Connection closed cleanly.")

    async def wait_stopped(self) -> None:
        """Wait until the connection enters its terminal stopped state."""
        if self.stopped:
            return

        await self._stopped_event.wait()

    async def close(self) -> None:
        """Close the connection gracefully and clean up."""
        async with self._connection_lock:
            await self._close_locked()

    async def wait_reconnected(self) -> None:
        """Wait for connection to be re-established after being lost.

        Blocks indefinitely until a reconnection occurs.
        Raises if the connection is terminally stopped.
        """
        self._assert_not_stopped()

        self._reconnected_event.clear()
        reconnected_waiter = asyncio.create_task(self._reconnected_event.wait())
        stopped_waiter = asyncio.create_task(self._stopped_event.wait())

        done, pending = await asyncio.wait(
            {reconnected_waiter, stopped_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        if stopped_waiter in done:
            self._assert_not_stopped()

    async def _verify_protocol(self) -> bool:
        """Verify that the panel accepts protocol frames on this transport."""
        if not self._transport.connected:
            _LOGGER.info(
                "Skipping protocol verification because the transport is not connected."
            )
            return False

        try:
            probe = SatelWriteMessage(SatelWriteCommand.RTC_AND_STATUS)

            await self._transport.send_frame(probe.encode_frame())
            raw_response = await asyncio.wait_for(
                self._transport.read_frame(), timeout=MESSAGE_RESPONSE_TIMEOUT
            )
        except Exception as exc:
            _LOGGER.info(
                "Startup protocol verification failed while sending or reading "
                "the probe response: %s",
                exc,
                exc_info=True,
            )
            return False

        if not raw_response:
            _LOGGER.info(
                "Startup protocol verification failed: no response received from the "
                "panel."
            )
            return False

        return True

    async def _check_connection(self) -> bool:
        """Check if the connection is valid and the panel is responsive."""
        if not self._transport.connected:
            _LOGGER.info(
                "Skipping connection check because the transport is not connected."
            )
            return False

        try:
            data = await asyncio.wait_for(
                self._transport.read_initial_data(), timeout=0.1
            )

            if data is None:
                _LOGGER.info("Connection check failed: no initial data could be read.")
                return False

            # Satel returns a string starting with "Busy" when another client is connected
            if b"Busy" in data:
                _LOGGER.info(
                    "Connection check failed: panel reports busy because another "
                    "client is connected."
                )
                return False

            # Log any other data to debug other potential blocking situation
            _LOGGER.debug(
                "Connection check received initial data after connect: %s", data
            )

            # Encrypted panels appear to return opaque bytes immediately when the
            # session is already occupied. A healthy encrypted connection times out
            # here instead.
            if isinstance(self._transport, SatelEncryptedTransport) and data:
                _LOGGER.info(
                    "Connection check failed: encrypted panel returned unexpected "
                    "initial data, so the session is treated as busy or unavailable."
                )
                return False

        except asyncio.TimeoutError:
            # Timeout is fine, it means we can actually read data
            pass
        except Exception as exc:
            _LOGGER.debug("Connection check failed: %s", exc, exc_info=True)
            return False

        return True
