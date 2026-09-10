"""Connection management for Satel Integra panel."""

import asyncio
import inspect
import logging

from satel_integra.commands import SatelReadCommand
from satel_integra.const import (
    MESSAGE_RESPONSE_TIMEOUT,
    ConnectionStateCallback,
    UnsubscribeCallback,
)
from satel_integra.exceptions import (
    SatelConnectFailedError,
    SatelConnectionInitializationError,
    SatelConnectionStoppedError,
    SatelPanelBusyError,
)
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
        self._connection_state_callbacks: list[ConnectionStateCallback] = []
        self._ready = False
        self._transport._set_connection_lost_callback(self._transport_connection_lost)

        self._stopped = False
        self._stopped_event = asyncio.Event()
        self._connection_lock = asyncio.Lock()  # Prevent concurrent connect/close
        self._reconnected_event = (
            asyncio.Event()
        )  # Signals when connection is re-established
        self._had_connection = False
        self._last_outbound_activity: float | None = None
        self._generation = 0

    @property
    def connected(self) -> bool:
        """Return True if the connection is validated and ready for use."""
        return self._ready

    @property
    def stopped(self) -> bool:
        """Return True if the connection is stopped."""
        return self._stopped

    @property
    def last_outbound_activity(self) -> float | None:
        """Return the loop timestamp of the last outbound panel command."""
        return self._last_outbound_activity

    @property
    def generation(self) -> int:
        """Return the current successful connection generation."""
        return self._generation

    def _assert_not_stopped(self) -> None:
        """Raise if the connection is in a terminal stopped state."""
        if self.stopped:
            raise SatelConnectionStoppedError("Connection is stopped")

    def add_connection_state_callback(
        self, callback: ConnectionStateCallback
    ) -> UnsubscribeCallback:
        """Register a ready-state callback and return a function to remove it."""
        self._connection_state_callbacks.append(callback)
        subscribed = True

        def unsubscribe() -> None:
            nonlocal subscribed

            if not subscribed:
                return

            subscribed = False
            for index, registered_callback in enumerate(
                self._connection_state_callbacks
            ):
                if registered_callback is callback:
                    del self._connection_state_callbacks[index]
                    break

        return unsubscribe

    async def _set_ready_state(self, ready: bool) -> None:
        """Notify callbacks when the verified connection state changes."""
        if ready == self._ready:
            return
        self._ready = ready
        for callback in tuple(self._connection_state_callbacks):
            try:
                result = callback()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                _LOGGER.exception("Error in connection state callback: %s", exc)

    async def _transport_connection_lost(self) -> None:
        """Clear ready state when the transport resets its connection."""
        await self._set_ready_state(False)

    def _now(self) -> float:
        """Return the running loop's monotonic time for interval tracking."""
        return asyncio.get_running_loop().time()

    async def _connect(self, verify_connection: bool = True) -> None:
        """Establish TCP connection. Must be called with _connection_lock held."""
        if self.stopped:
            _LOGGER.debug("Connection is closed, skipping connection")
            raise SatelConnectionStoppedError("Connection is stopped")

        if self.connected:
            _LOGGER.debug("Already connected, skipping connection")
            return

        _LOGGER.debug("Connecting to Satel Integra at %s:%s...", self._host, self._port)

        try:
            await self._transport.connect()
        except SatelConnectFailedError:
            _LOGGER.debug("Unable to establish TCP connection.")
            await self._close_locked(stop=False)
            raise

        if verify_connection:
            _LOGGER.debug("TCP connection established, verifying panel responsiveness")
            try:
                await self._check_connection()
            except asyncio.CancelledError:
                _LOGGER.debug(
                    "Connection readiness validation was cancelled, closing the "
                    "transport."
                )
                await self._close_locked(stop=False)
                raise
            except SatelPanelBusyError:
                _LOGGER.debug(
                    "Connected to the panel, but it is not ready for use. "
                    "Another client may already be connected, or the panel may "
                    "still be busy."
                )
                await self._close_locked(stop=False)
                raise
            except SatelConnectionInitializationError:
                _LOGGER.debug(
                    "Connected to the panel, but startup readiness validation failed."
                )
                await self._close_locked(stop=False)
                raise

            _LOGGER.debug("TCP connection established, verifying protocol round-trip")
            try:
                await self._verify_protocol()
            except asyncio.CancelledError:
                _LOGGER.debug(
                    "Protocol validation was cancelled, closing the transport."
                )
                await self._close_locked(stop=False)
                raise
            except SatelConnectionInitializationError:
                _LOGGER.debug(
                    "Connected to the panel, but startup validation failed. "
                    "Check that the integration key and encryption settings match "
                    "the panel configuration."
                )
                await self._close_locked(stop=False)
                raise

        else:
            _LOGGER.debug(
                "TCP connection established, skipping connection health check."
            )

        was_reconnection = self._had_connection
        self._generation += 1
        # Finalize durable state before callbacks, which may suspend or be
        # cancelled.
        self._had_connection = True

        _LOGGER.debug("Connected to Satel Integra.")
        try:
            await self._set_ready_state(True)
        finally:
            # Do not wake reconnection monitors until callback delivery exits.
            if was_reconnection:
                self._reconnected_event.set()

    async def connect(self, verify_connection: bool = True) -> None:
        """Establish TCP connection with a single attempt (no retries).

        Acquires lock internally. Suitable for setup validation where a single
        connection failure should not trigger automatic retries.
        """
        async with self._connection_lock:
            if self.stopped:
                raise SatelConnectionStoppedError("Connection is stopped")
            if self.connected:
                return
            await self._connect(verify_connection=verify_connection)

    async def read_frame(self) -> bytes | None:
        """Read a raw frame from the panel."""
        return await self._transport.read_frame()

    async def send_frame(self, frame: bytes) -> bool:
        """Send a raw frame to the panel."""
        sent = await self._transport.send_frame(frame)
        if sent:
            self._last_outbound_activity = self._now()
        return sent

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
                try:
                    await self._connect()
                    return
                except (
                    SatelConnectFailedError,
                    SatelConnectionInitializationError,
                    SatelPanelBusyError,
                ):
                    self._assert_not_stopped()

            self._assert_not_stopped()
            _LOGGER.debug(
                "Connection failed, retrying in %ss...", self._reconnection_timeout
            )
            await asyncio.sleep(self._reconnection_timeout)

    async def _close_locked(self, stop: bool = True) -> None:
        """Close the connection while the lock is already held."""
        if self.stopped:
            return

        _LOGGER.debug("Closing connection...")
        await self._transport.close()
        self._last_outbound_activity = None

        if stop:
            self._stopped = True
            self._stopped_event.set()
            _LOGGER.debug("Connection closed cleanly.")

    async def wait_stopped(self) -> None:
        """Wait until the connection enters its terminal stopped state."""
        if self.stopped:
            return

        await self._stopped_event.wait()

    async def close(self) -> None:
        """Close the connection gracefully and clean up."""
        async with self._connection_lock:
            await self._close_locked()

    async def disconnect(self) -> None:
        """Drop the current transport without terminally stopping the client."""
        async with self._connection_lock:
            await self._close_locked(stop=False)

    async def wait_reconnected(self) -> None:
        """Wait for connection to be re-established after being lost.

        Blocks indefinitely until a reconnection occurs.
        Raises if the connection is terminally stopped.
        """
        self._assert_not_stopped()

        self._reconnected_event.clear()
        reconnected_waiter = asyncio.create_task(self._reconnected_event.wait())
        stopped_waiter = asyncio.create_task(self._stopped_event.wait())

        waiters = {reconnected_waiter, stopped_waiter}
        try:
            done, _ = await asyncio.wait(
                waiters,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            # These tasks are implementation details of this coroutine. In
            # particular, cancelling the reconnection monitor during shutdown
            # must not leave either Event.wait() task orphaned.
            for task in waiters:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*waiters, return_exceptions=True)

        if stopped_waiter in done:
            self._assert_not_stopped()

    async def _verify_protocol(self) -> None:
        """Verify that the panel accepts protocol frames on this transport."""
        if not self._transport.connected:
            _LOGGER.debug(
                "Skipping protocol verification because the transport is not connected."
            )
            raise SatelConnectionInitializationError(
                "Cannot verify protocol without an active transport connection"
            )

        try:
            probe = SatelWriteMessage(SatelReadCommand.RTC_AND_STATUS)

            await self.send_frame(probe.encode_frame())
            raw_response = await asyncio.wait_for(
                self.read_frame(), timeout=MESSAGE_RESPONSE_TIMEOUT
            )
        except asyncio.TimeoutError as exc:
            _LOGGER.debug(
                "Startup protocol verification timed out after %ss while waiting "
                "for the probe response.",
                MESSAGE_RESPONSE_TIMEOUT,
            )
            raise SatelConnectionInitializationError(
                "Panel did not respond to the startup protocol probe before timeout"
            ) from exc
        except Exception as exc:
            _LOGGER.debug(
                "Startup protocol verification failed while sending or reading "
                "the probe response: %s",
                exc,
                exc_info=True,
            )
            raise SatelConnectionInitializationError(
                "Panel did not complete startup protocol verification"
            ) from exc

        if not raw_response:
            _LOGGER.debug(
                "Startup protocol verification failed: no response received from the "
                "panel."
            )
            raise SatelConnectionInitializationError(
                "Panel did not respond to the startup protocol probe"
            )

    async def _check_connection(self) -> None:
        """Check if the connection is valid and the panel is responsive."""
        if not self._transport.connected:
            _LOGGER.debug(
                "Skipping connection check because the transport is not connected."
            )
            raise SatelConnectionInitializationError(
                "Cannot validate the panel without an active transport connection"
            )

        try:
            data = await asyncio.wait_for(
                self._transport.read_initial_data(), timeout=0.1
            )
        except asyncio.TimeoutError:
            # Timeout is fine, it means we can actually read data
            return
        except Exception as exc:
            _LOGGER.debug("Connection check failed: %s", exc, exc_info=True)
            raise SatelConnectionInitializationError(
                "Panel failed connection readiness checks"
            ) from exc

        if data is None:
            _LOGGER.debug("Connection check failed: no initial data could be read.")
            raise SatelConnectionInitializationError(
                "Panel did not provide initial data after connecting"
            )

        # Satel returns a string starting with "Busy" when another client is connected
        if b"Busy" in data:
            _LOGGER.debug(
                "Connection check failed: panel reports busy because another "
                "client is connected."
            )
            raise SatelPanelBusyError(
                "Panel reports busy because another client is connected"
            )

        # Log any other data to debug other potential blocking situation
        _LOGGER.debug("Connection check received initial data after connect: %s", data)

        # Encrypted panels appear to return opaque bytes immediately when the
        # session is already occupied. A healthy encrypted connection times out
        # here instead.
        if isinstance(self._transport, SatelEncryptedTransport) and data:
            _LOGGER.debug(
                "Connection check failed: encrypted panel returned unexpected "
                "initial data, so the session is treated as busy or unavailable."
            )
            raise SatelPanelBusyError(
                "Encrypted panel returned startup data indicating the session is busy"
            )
