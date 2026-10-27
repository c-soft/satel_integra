"""Main module for Satel Integra alarm system client."""

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from typing import TypeVar, cast, overload
from warnings import warn

from satel_integra.callbacks import (
    AsyncStateChangeCallback,
    StateChangeCallback,
    UnsubscribeCallback,
)
from satel_integra.commands import SatelReadCommand, SatelWriteCommand
from satel_integra.connection import SatelConnection
from satel_integra.const import (
    KEEPALIVE_INTERVAL,
    MONITORING_REFRESH_INTERVAL,
)
from satel_integra.exceptions import (
    SatelCommandRejectedError,
    SatelConnectFailedError,
    SatelConnectionInitializationError,
    SatelConnectionStoppedError,
    SatelMonitoringStartError,
    SatelNoResponseError,
    SatelPanelBusyError,
    SatelUnexpectedResponseError,
)
from satel_integra.message_handlers import (
    MessageHandlerRegistry,
    SatelSubscriptions,
)
from satel_integra.messages import (
    SatelDeviceSelector,
    SatelReadMessage,
    SatelReadMessageData,
    SatelTypedReadMessage,
    SatelWriteMessage,
)
from satel_integra.models import (
    SatelCommandResult,
    SatelCommunicationModuleInfo,
    SatelOutputInfo,
    SatelPanelInfo,
    SatelPanelScan,
    SatelPartitionInfo,
    SatelResultCode,
    SatelRtcAndStatus,
    SatelZoneInfo,
    SatelZoneTemperature,
)
from satel_integra.queue import SatelMessageQueue
from satel_integra.state import AlarmState, SatelState
from satel_integra.utils import encode_bitmask_le, encode_device_number

TReadData = TypeVar("TReadData", bound=SatelReadMessageData)

if sys.version_info >= (3, 13):
    from warnings import deprecated
else:
    from functools import wraps

    def deprecated(message):
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                warn(message, DeprecationWarning, stacklevel=2)
                return func(*args, **kwargs)

            return wrapper

        return decorator


_LOGGER = logging.getLogger(__name__)


def _validate_user_code(code: str) -> None:
    """Validate the supported user-code length."""
    if not 4 <= len(code) <= 16:
        raise ValueError("code must contain between 4 and 16 characters")


async def _scan_devices[TDeviceInfo](
    device_type: str,
    read_device: Callable[[int], Awaitable[TDeviceInfo | None]],
    capacity: int,
    on_device_scanned: Callable[[], None] | None = None,
) -> dict[int, TDeviceInfo]:
    """Read every address, omitting addresses rejected as unconfigured."""
    devices: dict[int, TDeviceInfo] = {}
    for device_number in range(1, capacity + 1):
        try:
            device_info = await read_device(device_number)
        except SatelCommandRejectedError as err:
            # The protocol has no dedicated "unconfigured device" result. Panels
            # use the generic 0x08 result for unused 0xEE device addresses.
            if err.result.code != SatelResultCode.OTHER_ERROR:
                raise
            if on_device_scanned is not None:
                on_device_scanned()
            continue

        if device_info is None:
            raise SatelNoResponseError(
                f"No response while scanning {device_type} {device_number}"
            )
        devices[device_number] = device_info
        if on_device_scanned is not None:
            on_device_scanned()

    return devices


class AsyncSatel:
    """Asynchronous interface to talk to Satel Integra alarm system."""

    def __init__(
        self,
        host: str,
        port: int,
        monitored_zones: list[int] = [],
        monitored_outputs: list[int] = [],
        partitions: list[int] = [],
        integration_key: str | None = None,
    ):
        """Init the Satel alarm data."""
        self._connection = SatelConnection(host, port, integration_key=integration_key)
        self._queue = SatelMessageQueue(
            self._send_encoded_frame,
            connected=lambda: self._connection.connected,
            generation=lambda: self._connection.generation,
        )
        self._running_tasks: set[asyncio.Task[object]] = set()
        self._closing = False
        self._connection_unavailable_logged = False
        self._connection.add_connection_state_callback(self._connection_state_changed)

        self._monitored_zones: list[int] = monitored_zones
        self._state = SatelState()

        self._monitored_outputs: list[int] = monitored_outputs

        self._partitions: list[int] = partitions

        self._alarm_status_callback: Callable[[], None] | None = None
        # Connection generation on which monitoring was acknowledged (-1 = none).
        self._monitoring_generation = -1
        self._zone_changed_callback: Callable[[dict[int, int]], None] | None = None
        self._output_changed_callback: Callable[[dict[int, int]], None] | None = None

        self._subscriptions = SatelSubscriptions()
        self._subscriptions.partitions.add(self._notify_alarm_status_changed)
        self._subscriptions.zones.add(self._notify_zone_changed)
        self._subscriptions.outputs.add(self._notify_output_changed)
        self._message_handler_registry = MessageHandlerRegistry(
            self._state,
            self._subscriptions,
        )

    async def start_monitoring(self) -> None:
        """Start monitoring, raising if no successful acknowledgement is received."""

        monitored_commands = [
            SatelReadCommand.ZONES_VIOLATED,
            SatelReadCommand.ZONES_TAMPER,
            SatelReadCommand.ZONES_BYPASS,
            SatelReadCommand.PARTITIONS_ARMED_MODE0,
            SatelReadCommand.PARTITIONS_ARMED_MODE1,
            SatelReadCommand.PARTITIONS_ARMED_MODE2,
            SatelReadCommand.PARTITIONS_ARMED_MODE3,
            SatelReadCommand.PARTITIONS_ARMED_SUPPRESSED,
            SatelReadCommand.PARTITIONS_ENTRY_TIME,
            SatelReadCommand.PARTITIONS_EXIT_COUNTDOWN_OVER_10,
            SatelReadCommand.PARTITIONS_EXIT_COUNTDOWN_UNDER_10,
            SatelReadCommand.PARTITIONS_ALARM,
            SatelReadCommand.PARTITIONS_FIRE_ALARM,
            SatelReadCommand.OUTPUTS_STATE,
            SatelReadCommand.RTC_AND_STATUS,
            SatelReadCommand.TROUBLES_PART_1,
        ]

        generation = self._connection.generation
        monitored_commands_bitmask = encode_bitmask_le(
            [cmd.value + 1 for cmd in monitored_commands], 12
        )

        msg = SatelWriteMessage(
            SatelWriteCommand.START_MONITORING,
            raw_data=bytearray(monitored_commands_bitmask),
        )

        try:
            monitoring_result = await self._send_data_and_wait(msg, SatelCommandResult)
        except (SatelCommandRejectedError, SatelUnexpectedResponseError) as err:
            raise SatelMonitoringStartError(
                "Monitoring startup did not receive a successful acknowledgement"
            ) from err

        if monitoring_result is None:
            raise SatelMonitoringStartError("No response to monitoring request")

        was_ready = self.monitoring_ready
        self._monitoring_generation = generation
        if not was_ready and self.monitoring_ready:
            # Let consumers update availability now that data is flowing.
            await self._connection.notify_state_changed()

        _LOGGER.debug("Monitoring started")

    def _notify_zone_changed(self) -> None:
        """Notify the legacy zone callback from current state."""
        status: dict[int, int] = {}
        for zone in self._monitored_zones:
            zone_state = self._state.zones.get(zone)
            status[zone] = int(zone_state is not None and zone_state.violated is True)

        _LOGGER.debug("Returning status: %s", status)

        if self._zone_changed_callback:
            self._zone_changed_callback(status)

    def _notify_output_changed(self) -> None:
        """Notify the legacy output callback from current state."""
        status: dict[int, int] = {}
        for output in self._monitored_outputs:
            output_state = self._state.outputs.get(output)
            status[output] = int(
                output_state is not None and output_state.active is True
            )

        _LOGGER.debug("Returning status: %s", status)

        if self._output_changed_callback:
            self._output_changed_callback(status)

    def _notify_alarm_status_changed(self) -> None:
        """Notify the legacy alarm status callback."""
        if self._alarm_status_callback:
            self._alarm_status_callback()

    # region Core logic
    async def start(self, enable_monitoring=True):
        """Start the client, including queue, reading loop and keepalive."""
        try:
            await self._connection.ensure_connected()
        except SatelConnectionStoppedError:
            return

        self._start_task(self._watch_connection_stopped())
        self._start_task(self._reading_loop())

        await self._queue.start()

        self._start_task(self._keepalive_loop())

        try:
            await self._initialize_panel_state()
            if enable_monitoring:
                await self.start_monitoring()
        except (Exception, asyncio.CancelledError):
            await self.close()
            raise

        if enable_monitoring:
            # Started after the first acknowledgement, so the loop does not
            # send a second monitoring request on the same connection.
            self._start_task(self._monitor_reconnection_loop())

    async def _initialize_panel_state(self) -> None:
        """Discover panel capabilities without preventing monitoring startup."""
        try:
            panel_info = await self.read_panel_info()
        except (SatelCommandRejectedError, SatelUnexpectedResponseError) as err:
            _LOGGER.warning("Panel information discovery failed: %s", err)
            return

        if panel_info is None:
            _LOGGER.warning("Panel information discovery returned no response")
            return

        self._state.configure_for_panel(panel_info)

    def _start_task(self, coro: Awaitable[object]) -> asyncio.Task[object]:
        """Create and track a background task."""
        task = asyncio.create_task(coro)
        self._running_tasks.add(task)
        task.add_done_callback(self._running_tasks.discard)
        return task

    async def _keepalive_loop(self):
        """A workaround for Satel Integra disconnecting after 25s.

        Every interval it sends some random question to the device, ignoring
        answer - just to keep connection alive.
        """
        loop = asyncio.get_running_loop()
        _LOGGER.debug(
            "Keepalive loop started with %.1fs idle timeout", KEEPALIVE_INTERVAL
        )

        while True:
            now = loop.time()
            last_outbound_activity = self._connection.last_outbound_activity

            if last_outbound_activity is None:
                last_outbound_activity = now

            # Sleep until next possible interval
            deadline = last_outbound_activity + KEEPALIVE_INTERVAL
            sleep_duration = max(0, deadline - now)
            await asyncio.sleep(sleep_duration)

            now = loop.time()
            if (wakeup_lag := now - deadline) > 1:
                _LOGGER.debug(
                    "Keepalive woke up %.3fs after the idle deadline",
                    wakeup_lag,
                )

            if self.stopped:
                return
            if not self.connected:
                _LOGGER.debug("Keepalive suppressed because the connection is down")
                continue

            if (observed := self._connection.last_outbound_activity) is not None:
                last_outbound_activity = observed

            # Check if we exceeded the interval after sleeping
            idle_for = now - last_outbound_activity
            if idle_for < KEEPALIVE_INTERVAL:
                _LOGGER.debug(
                    "Keepalive skipped because outbound activity was seen %.3fs ago",
                    idle_for,
                )
                continue

            data = SatelWriteMessage(SatelReadCommand.RTC_AND_STATUS)
            _LOGGER.debug(
                "Keepalive sending after %.3fs of outbound inactivity", idle_for
            )
            connection_generation = self._connection.generation

            try:
                result = await self._send_data_and_wait(data, SatelRtcAndStatus)
                if result is None:
                    # Check if connection is still the same and mark as lost if so
                    # This can happen when network is down, but the connection didn't really close
                    if (
                        self.connected
                        and self._connection.generation == connection_generation
                    ):
                        _LOGGER.debug(
                            "Keepalive timed out on current connection; "
                            "marking connection as lost"
                        )
                        await self._connection.disconnect()
                    else:
                        _LOGGER.debug(
                            "Ignoring stale keepalive timeout from connection "
                            "generation %s",
                            connection_generation,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("Keepalive send failed")

    async def _watch_connection_stopped(self):
        """Stop local background work once the connection becomes terminally stopped."""
        try:
            await self._connection.wait_stopped()
            await self.close()
        except asyncio.CancelledError:
            return

    async def _reading_loop(self):
        try:
            while True:
                await self._connection.ensure_connected()

                msg = await self._read_data()

                if not msg:
                    continue

                self._queue.on_message_received(msg)

                # One frame that cannot be handled must not end the reading
                # loop: the loop would exit with TCP still open, so there
                # would be no reconnection and no more data.
                try:
                    handled = self._message_handler_registry.dispatch(msg)
                except Exception as ex:
                    _LOGGER.warning(
                        "Dropping frame %s, handler failed: %s", msg.cmd, ex
                    )
                    continue

                if not handled:
                    _LOGGER.debug("No handler for command: %s", msg.cmd)

        except SatelConnectionStoppedError:
            return
        except asyncio.CancelledError:
            _LOGGER.info("_reading_loop loop cancelled.")
        except Exception as ex:
            _LOGGER.exception("Error in _reading_loop loop, %s", ex)

    async def _monitor_reconnection_loop(self):
        """Keep monitoring enabled on every connection.

        This task is only created when monitoring is enabled.

        - On a connection without acknowledged monitoring, retry the request
          with backoff until it succeeds or the connection changes. Previously a
          single unanswered request left monitoring off until the next
          reconnection, which keepalive could postpone indefinitely: the client
          looked connected while no state updates arrived.
        - Wait for a newer ready connection by generation rather than a shared
          event, so a reconnection during a monitoring request is not missed.
        - On an unchanged connection, re-send the request every
          MONITORING_REFRESH_INTERVAL; the module then re-sends the state of all
          monitored commands, which recovers any lost update.
        """
        while True:
            if self._connection.stopped:
                return
            try:
                generation = self._connection.generation
                if self.connected and self._monitoring_generation != generation:
                    await self._start_monitoring_until_ok()
                    continue
                try:
                    await asyncio.wait_for(
                        self._connection.wait_ready_after(generation),
                        MONITORING_REFRESH_INTERVAL,
                    )
                except TimeoutError:
                    if self.connected:
                        _LOGGER.debug("Refreshing monitoring on the same connection")
                        try:
                            await self.start_monitoring()
                        except SatelMonitoringStartError as ex:
                            _LOGGER.warning("Monitoring refresh failed: %s", ex)
                            self._monitoring_generation = -1
            except SatelConnectionStoppedError:
                return
            except asyncio.CancelledError:
                break
            except Exception as ex:
                _LOGGER.exception("Error in _monitor_reconnection: %s", ex)
                await asyncio.sleep(1)

    async def _start_monitoring_until_ok(self) -> None:
        """Enable monitoring on the current connection.

        Retries with backoff (1 s up to 30 s) until acknowledged or until the
        connection is lost or replaced.
        """
        generation = self._connection.generation
        delay = 1.0
        while self.connected and self._connection.generation == generation:
            try:
                await self.start_monitoring()
            except SatelConnectionStoppedError:
                raise
            except Exception as ex:
                _LOGGER.warning(
                    "Starting monitoring failed (%s), retrying in %.0fs", ex, delay
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
            else:
                _LOGGER.info("Monitoring enabled")
                return

    @deprecated(
        "register_callbacks is deprecated; use add_alarm_status_callback, "
        "add_zone_changed_callback, and add_output_changed_callback instead"
    )
    def register_callbacks(
        self,
        alarm_status_callback: Callable[[], None] | None = None,
        zone_changed_callback: Callable[[dict[int, int]], None] | None = None,
        output_changed_callback: Callable[[dict[int, int]], None] | None = None,
    ):
        """Register callback handlers for events."""
        if alarm_status_callback:
            self._alarm_status_callback = alarm_status_callback
        if zone_changed_callback:
            self._zone_changed_callback = zone_changed_callback
        if output_changed_callback:
            self._output_changed_callback = output_changed_callback

    def add_alarm_status_callback(
        self, callback: StateChangeCallback
    ) -> UnsubscribeCallback:
        """Subscribe to alarm status changes and return a function to unsubscribe."""
        return self._subscriptions.partitions.add(callback)

    def add_zone_changed_callback(
        self, callback: StateChangeCallback
    ) -> UnsubscribeCallback:
        """Subscribe to zone changes and return a function to unsubscribe."""
        return self._subscriptions.zones.add(callback)

    def add_output_changed_callback(
        self, callback: StateChangeCallback
    ) -> UnsubscribeCallback:
        """Subscribe to output changes and return a function to unsubscribe."""
        return self._subscriptions.outputs.add(callback)

    def add_system_status_callback(
        self, callback: StateChangeCallback
    ) -> UnsubscribeCallback:
        """Subscribe to system status changes and return a function to unsubscribe."""
        return self._subscriptions.system_status.add(callback)

    @property
    def state(self) -> SatelState:
        """Return the current panel state."""
        return self._state

    @property
    @deprecated("Use state.zones instead")
    def violated_zones(self) -> list[int]:
        """Return violated zones (deprecated; use state.zones)."""
        return [
            zone for zone, state in self._state.zones.items() if state.violated is True
        ]

    @property
    @deprecated("Use state.outputs instead")
    def violated_outputs(self) -> list[int]:
        """Return violated outputs (deprecated; use state.outputs)."""
        return [
            output
            for output, state in self._state.outputs.items()
            if state.active is True
        ]

    @property
    @deprecated("Use state.partition_states instead")
    def partition_states(self) -> dict[AlarmState, list[int]]:
        """Return partition states (deprecated; use state.partition_states)."""
        return self._state.partition_states

    def _connection_state_changed(self) -> None:
        """Log user-facing connection availability changes once per outage."""
        if self._closing or self.stopped:
            return

        if not self.connected:
            if not self._connection_unavailable_logged:
                _LOGGER.info("Connection to Satel Integra panel lost")
                self._connection_unavailable_logged = True
            return

        if self._connection_unavailable_logged:
            _LOGGER.info("Connection to Satel Integra panel restored")
            self._connection_unavailable_logged = False

    def add_connection_status_callback(
        self, callback: AsyncStateChangeCallback
    ) -> UnsubscribeCallback:
        """Add a connection-status callback and return a function to remove it."""
        return self._connection.add_connection_state_callback(callback)

    # endregion

    # region Write actions
    async def arm(
        self,
        code: str,
        partition_list: list[int],
        mode: int = 0,
        *,
        wait_for_result: bool = False,
    ) -> SatelCommandResult | None:
        """Arm partitions, optionally returning the panel's 0xEF result. Modes 0 through 3 are supported."""
        _validate_user_code(code)

        if not 0 <= mode <= 3:
            raise ValueError("mode must be between 0 and 3")

        _LOGGER.debug("Sending arm command, mode: %s!", mode)

        mode_command = SatelWriteCommand(SatelWriteCommand.PARTITIONS_ARM_MODE_0 + mode)

        msg = SatelWriteMessage(mode_command, code=code, partitions=partition_list)

        return await self._send_control_message(msg, wait_for_result)

    async def disarm(
        self,
        code: str,
        partition_list: list[int],
        *,
        wait_for_result: bool = False,
    ) -> SatelCommandResult | None:
        """Disarm partitions, optionally returning the panel's 0xEF result."""
        _validate_user_code(code)

        _LOGGER.info("Sending disarm command.")

        msg = SatelWriteMessage(
            SatelWriteCommand.PARTITIONS_DISARM, code=code, partitions=partition_list
        )

        return await self._send_control_message(msg, wait_for_result)

    async def clear_alarm(
        self,
        code: str,
        partition_list: list[int],
        *,
        wait_for_result: bool = False,
    ) -> SatelCommandResult | None:
        """Clear partition alarms, optionally returning the panel's 0xEF result."""
        _validate_user_code(code)

        _LOGGER.info("Sending clear the alarm command.")

        msg = SatelWriteMessage(
            SatelWriteCommand.PARTITIONS_CLEAR_ALARM,
            code=code,
            partitions=partition_list,
        )

        return await self._send_control_message(msg, wait_for_result)

    async def set_output(
        self,
        code: str,
        output_id: int,
        state: bool,
        *,
        wait_for_result: bool = False,
    ) -> SatelCommandResult | None:
        """Set an output state, optionally returning the panel's 0xEF result."""
        _validate_user_code(code)

        _LOGGER.debug("Set output %s to %s", output_id, state)

        mode_command = (
            SatelWriteCommand.OUTPUTS_ON if state else SatelWriteCommand.OUTPUTS_OFF
        )
        msg = SatelWriteMessage(mode_command, code=code, zones_or_outputs=[output_id])
        return await self._send_control_message(msg, wait_for_result)

    async def read_temperature(self, zone_id: int) -> float | None:
        """Read the temperature for a single zone sensor."""
        device_number = encode_device_number(zone_id)
        msg = SatelWriteMessage(
            SatelReadCommand.ZONE_TEMPERATURE, raw_data=bytearray([device_number])
        )
        response_data = await self._send_data_and_wait(
            msg,
            SatelZoneTemperature,
        )

        if response_data is None:
            return None
        if response_data.zone_id != zone_id:
            err = (
                "Temperature response zone mismatch: "
                f"expected {zone_id}, got {response_data.zone_id}"
            )
            raise ValueError(err)

        return response_data.temperature

    async def read_temperatures(self, zone_ids: list[int]) -> dict[int, float | None]:
        """Read temperatures for multiple zone sensors sequentially."""
        temperatures: dict[int, float | None] = {}

        for zone_id in zone_ids:
            try:
                temperatures[zone_id] = await self.read_temperature(zone_id)
            except Exception as err:
                _LOGGER.warning(
                    "Error reading temperature for zone %s: %s", zone_id, err
                )
                temperatures[zone_id] = None

        return temperatures

    async def read_zone_info(self, zone_id: int) -> SatelZoneInfo | None:
        """Read metadata for a single zone."""
        device_number = encode_device_number(zone_id)
        msg = SatelWriteMessage(
            SatelReadCommand.READ_DEVICE_NAME,
            raw_data=bytearray(
                [SatelDeviceSelector.ZONE_WITH_PARTITION_ASSIGNMENT, device_number]
            ),
        )
        response_data = await self._send_data_and_wait(
            msg,
            SatelZoneInfo,
        )

        if response_data is None:
            return None
        if response_data.device_number != zone_id:
            err = (
                "Zone info response zone mismatch: "
                f"expected {zone_id}, got {response_data.device_number}"
            )
            raise ValueError(err)

        return response_data

    async def read_partition_info(self, partition_id: int) -> SatelPartitionInfo | None:
        """Read metadata for a single partition."""
        if not 1 <= partition_id <= 32:
            raise ValueError("partition_id must be between 1 and 32")

        msg = SatelWriteMessage(
            SatelReadCommand.READ_DEVICE_NAME,
            raw_data=bytearray(
                [SatelDeviceSelector.PARTITION_WITH_OBJECT_ASSIGNMENT, partition_id]
            ),
        )
        response_data = await self._send_data_and_wait(
            msg,
            SatelPartitionInfo,
        )

        if response_data is None:
            return None
        if response_data.device_number != partition_id:
            err = (
                "Partition info response partition mismatch: "
                f"expected {partition_id}, got {response_data.device_number}"
            )
            raise ValueError(err)

        return response_data

    async def read_output_info(self, output_id: int) -> SatelOutputInfo | None:
        """Read metadata for a single output."""
        device_number = encode_device_number(output_id)
        msg = SatelWriteMessage(
            SatelReadCommand.READ_DEVICE_NAME,
            raw_data=bytearray([SatelDeviceSelector.OUTPUT, device_number]),
        )
        response_data = await self._send_data_and_wait(
            msg,
            SatelOutputInfo,
        )

        if response_data is None:
            return None
        if response_data.device_number != output_id:
            err = (
                "Output info response output mismatch: "
                f"expected {output_id}, got {response_data.device_number}"
            )
            raise ValueError(err)

        return response_data

    async def read_panel_info(self) -> SatelPanelInfo | None:
        """Read structured panel information."""
        msg = SatelWriteMessage(SatelReadCommand.INTEGRA_VERSION)
        return await self._send_data_and_wait(
            msg,
            SatelPanelInfo,
        )

    async def read_communication_module_info(
        self,
    ) -> SatelCommunicationModuleInfo | None:
        """Read structured communication module information."""
        msg = SatelWriteMessage(SatelReadCommand.MODULE_VERSION)
        return await self._send_data_and_wait(
            msg,
            SatelCommunicationModuleInfo,
        )

    async def scan_panel(
        self,
        *,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> SatelPanelScan:
        """Scan configurable devices using panel information cached at startup.

        ``on_progress`` is called after each completed device read with the
        number of completed reads and the total number of reads.

        Addresses rejected with the protocol's generic 0x08 result are omitted;
        panels use that result for unused 0xEE device addresses.
        """
        panel_info = self._state.panel_info
        if panel_info is None:
            raise SatelNoResponseError("Panel information is unavailable")

        model = panel_info.model
        if model is None:
            raise SatelUnexpectedResponseError(
                f"Cannot scan unknown panel type code {panel_info.type_code}"
            )

        total = model.max_partitions + model.max_zones + model.max_outputs
        completed = 0

        def report_progress() -> None:
            nonlocal completed
            completed += 1
            if on_progress is not None:
                on_progress(completed, total)

        partitions = await _scan_devices(
            "partition",
            self.read_partition_info,
            model.max_partitions,
            report_progress,
        )
        zones = await _scan_devices(
            "zone", self.read_zone_info, model.max_zones, report_progress
        )
        outputs = await _scan_devices(
            "output", self.read_output_info, model.max_outputs, report_progress
        )
        # Type code 0 is the default for unconfigured outputs, so omit them from the scan result.
        outputs = {
            device_number: output
            for device_number, output in outputs.items()
            if output.type_code != 0
        }

        return SatelPanelScan(
            panel_info=panel_info,
            partitions=partitions,
            zones=zones,
            outputs=outputs,
        )

    # endregion

    # region Data management
    async def _send_data(self, msg: SatelWriteMessage) -> None:
        """Add a message to the queue without waiting for its response."""
        await self._queue.add_message(msg, False)

    async def _send_control_message(
        self, msg: SatelWriteMessage, wait_for_result: bool
    ) -> SatelCommandResult | None:
        """Send a control message, optionally waiting for its result."""
        if not wait_for_result:
            await self._send_data(msg)
            return None

        result = await self._send_data_and_wait(msg, SatelCommandResult)
        if result is None:
            raise SatelNoResponseError(f"No result from the panel for {msg.cmd}")
        return result

    @overload
    async def _send_data_and_wait(
        self,
        msg: SatelWriteMessage,
    ) -> SatelReadMessage | None: ...

    @overload
    async def _send_data_and_wait(
        self,
        msg: SatelWriteMessage,
        expected_data_type: type[TReadData],
    ) -> TReadData | None: ...

    async def _send_data_and_wait(
        self,
        msg: SatelWriteMessage,
        expected_data_type: type[TReadData] | None = None,
    ) -> SatelReadMessage | SatelReadMessageData | None:
        """Add a message to the queue and optionally decode its response."""
        response = await self._queue.add_message(msg, True)

        if response is None:
            _LOGGER.debug(f"No response received for {msg.cmd}")
            return None

        if expected_data_type is None:
            return response

        if (
            isinstance(response, SatelTypedReadMessage)
            and response.data_type is SatelCommandResult
        ):
            result = cast(SatelCommandResult, response.data)

            if not result.is_success:
                raise SatelCommandRejectedError(msg.cmd, result)

        if (
            not isinstance(response, SatelTypedReadMessage)
            or response.data_type is not expected_data_type
        ):
            actual_type = (
                response.data_type.__name__
                if isinstance(response, SatelTypedReadMessage)
                else type(response).__name__
            )
            err = (
                f"Unexpected response type for {msg.cmd}: "
                f"expected {expected_data_type.__name__}, got {actual_type}"
            )
            raise SatelUnexpectedResponseError(err)

        return cast(TReadData, response.data)

    async def _send_encoded_frame(self, msg: SatelWriteMessage) -> None:
        """Encodes and actually sends message."""
        data = msg.encode_frame()

        await self._connection.send_frame(data)

    async def _read_data(self) -> SatelReadMessage | None:
        """Read data from the alarm."""

        try:
            data = await self._connection.read_frame()

            if not data:
                return None

            msg = SatelReadMessage.decode_frame(data)
            return msg

        except Exception as e:
            _LOGGER.exception("Error reading data: %s", e)
            return None

        finally:
            if self._alarm_status_callback:
                self._alarm_status_callback()

    # endregion

    # region Connection management
    @property
    def connected(self) -> bool:
        """Return true if there is connection to the alarm."""
        return self._connection.connected

    @property
    @deprecated("Use stopped instead")
    def closed(self) -> bool:
        """Return true if connection is closed."""
        return self._connection.stopped

    @property
    def stopped(self) -> bool:
        """Return true if connection is stopped."""
        return self._connection.stopped

    @property
    def monitoring_ready(self) -> bool:
        """Return true if connected and monitoring is acknowledged on this connection.

        A connection alone does not mean state updates are arriving; use this
        for availability. Connection state callbacks also fire when it changes.
        """
        return (
            self.connected
            and self._monitoring_generation == self._connection.generation
        )

    @overload
    @deprecated("Use connect with 'verify_connection' property instead")
    async def connect(self, *, check_busy: bool = True) -> bool: ...

    @overload
    async def connect(
        self, verify_connection: bool = True, *, raise_exceptions: bool = False
    ) -> bool: ...

    async def connect(
        self,
        verify_connection: bool = True,
        *,
        check_busy: bool | None = None,
        raise_exceptions: bool = False,
    ) -> bool:
        """Make a TCP connection to the alarm system."""
        if check_busy is not None:
            warn(
                "'check_busy' is deprecated; use 'verify_connection'",
                DeprecationWarning,
                stacklevel=2,
            )
            verify_connection = check_busy

        try:
            await self._connection.connect(verify_connection=verify_connection)
        except (
            SatelConnectFailedError,
            SatelConnectionInitializationError,
            SatelConnectionStoppedError,
            SatelPanelBusyError,
        ):
            if raise_exceptions:
                raise
            return False

        return True

    async def close(self):
        """Stop background tasks and close connection."""
        self._closing = True
        await self._connection.close()

        await self._cancel_running_tasks()
        await self._queue.stop()

    async def _cancel_running_tasks(self) -> None:
        """Cancel all tracked background tasks except the current one."""
        current_task = asyncio.current_task()
        tasks = [task for task in self._running_tasks if task is not current_task]

        for task in tasks:
            task.cancel()

        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._running_tasks.difference_update(tasks)

    # endregion
