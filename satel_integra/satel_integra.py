"""Main module for Satel Integra alarm system client."""

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from enum import Enum, unique
from typing import overload
from warnings import warn

from satel_integra.commands import SatelReadCommand, SatelWriteCommand
from satel_integra.connection import SatelConnection
from satel_integra.const import KEEPALIVE_INTERVAL, ConnectionStateCallback
from satel_integra.exceptions import (
    SatelConnectFailedError,
    SatelConnectionInitializationError,
    SatelConnectionStoppedError,
    SatelPanelBusyError,
    SatelUnexpectedResponseError,
)
from satel_integra.messages import (
    SatelDeviceSelector,
    SatelIntegraVersionReadMessage,
    SatelModuleVersionReadMessage,
    SatelReadMessage,
    SatelWriteMessage,
    SatelZoneInfoReadMessage,
    SatelZoneTemperatureReadMessage,
)
from satel_integra.models import (
    SatelCommunicationModuleInfo,
    SatelPanelInfo,
    SatelZoneInfo,
)
from satel_integra.queue import SatelMessageQueue
from satel_integra.utils import encode_bitmask_le, encode_zone_number

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


@unique
class AlarmState(Enum):
    """Represents status of the alarm."""

    ARMED_MODE0 = 0
    ARMED_MODE1 = 1
    ARMED_MODE2 = 2
    ARMED_MODE3 = 3
    ARMED_SUPPRESSED = 4
    ENTRY_TIME = 5
    EXIT_COUNTDOWN_OVER_10 = 6
    EXIT_COUNTDOWN_UNDER_10 = 7
    TRIGGERED = 8
    TRIGGERED_FIRE = 9
    DISARMED = 10


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
        self._queue = SatelMessageQueue(self._send_encoded_frame)
        self._running_tasks: set[asyncio.Task[object]] = set()
        self._closing = False
        self._connection_unavailable_logged = False
        self._connection.add_connection_state_callback(self._connection_state_changed)

        self._monitored_zones: list[int] = monitored_zones
        self.violated_zones: list[int] = []

        self._monitored_outputs: list[int] = monitored_outputs
        self.violated_outputs: list[int] = []

        self.partition_states: dict[AlarmState, list[int]] = {}
        self._partitions: list[int] = partitions

        self._alarm_status_callback: Callable[[], None] | None = None
        self._zone_changed_callback: Callable[[dict[int, int]], None] | None = None
        self._output_changed_callback: Callable[[dict[int, int]], None] | None = None

        self._message_handlers: dict[
            SatelReadCommand, Callable[[SatelReadMessage], None]
        ] = {
            SatelReadCommand.ZONES_VIOLATED: self._zones_violated,
            SatelReadCommand.PARTITIONS_ARMED_SUPPRESSED: lambda msg: (
                self._partitions_armed_state(AlarmState.ARMED_SUPPRESSED, msg)
            ),
            SatelReadCommand.PARTITIONS_ARMED_MODE0: lambda msg: (
                self._partitions_armed_state(AlarmState.ARMED_MODE0, msg)
            ),
            SatelReadCommand.PARTITIONS_ARMED_MODE2: lambda msg: (
                self._partitions_armed_state(AlarmState.ARMED_MODE2, msg)
            ),
            SatelReadCommand.PARTITIONS_ARMED_MODE3: lambda msg: (
                self._partitions_armed_state(AlarmState.ARMED_MODE3, msg)
            ),
            SatelReadCommand.PARTITIONS_ENTRY_TIME: lambda msg: (
                self._partitions_armed_state(AlarmState.ENTRY_TIME, msg)
            ),
            SatelReadCommand.PARTITIONS_EXIT_COUNTDOWN_OVER_10: lambda msg: (
                self._partitions_armed_state(AlarmState.EXIT_COUNTDOWN_OVER_10, msg)
            ),
            SatelReadCommand.PARTITIONS_EXIT_COUNTDOWN_UNDER_10: lambda msg: (
                self._partitions_armed_state(AlarmState.EXIT_COUNTDOWN_UNDER_10, msg)
            ),
            SatelReadCommand.PARTITIONS_ALARM: lambda msg: self._partitions_armed_state(
                AlarmState.TRIGGERED, msg
            ),
            SatelReadCommand.PARTITIONS_FIRE_ALARM: lambda msg: (
                self._partitions_armed_state(AlarmState.TRIGGERED_FIRE, msg)
            ),
            SatelReadCommand.OUTPUTS_STATE: self._outputs_changed,
            SatelReadCommand.PARTITIONS_ARMED_MODE1: lambda msg: (
                self._partitions_armed_state(AlarmState.ARMED_MODE1, msg)
            ),
            SatelReadCommand.RESULT: self._command_result,
        }

    async def start_monitoring(self):
        """Start monitoring for interesting events."""

        monitored_commands = [
            SatelReadCommand.ZONES_VIOLATED,
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
        ]

        monitored_commands_bitmask = encode_bitmask_le(
            [cmd.value + 1 for cmd in monitored_commands], 12
        )

        msg = SatelWriteMessage(
            SatelWriteCommand.START_MONITORING,
            raw_data=bytearray(monitored_commands_bitmask),
        )

        monitoring_result = await self._send_data_and_wait(msg)

        if monitoring_result is None:
            _LOGGER.warning("Start monitoring - no data!")
            return

        if monitoring_result.msg_data != b"\xff":
            _LOGGER.warning("Monitoring not accepted.")
            return

        _LOGGER.debug("Monitoring started")

    def _zones_violated(self, msg: SatelReadMessage):
        status: dict[int, int] = {}

        violated_zones = msg.get_active_bits(32)
        self.violated_zones = violated_zones
        _LOGGER.debug("Violated zones: %s", violated_zones)
        for zone in self._monitored_zones:
            status[zone] = 1 if zone in violated_zones else 0

        _LOGGER.debug("Returning status: %s", status)

        if self._zone_changed_callback:
            self._zone_changed_callback(status)

    def _outputs_changed(self, msg: SatelReadMessage):
        """0x17   outputs state 0x17   + 16/32 bytes"""

        status: dict[int, int] = {}

        output_states = msg.get_active_bits(32)
        self.violated_outputs = output_states
        _LOGGER.debug(
            "Output states: %s, monitored outputs: %s",
            output_states,
            self._monitored_outputs,
        )
        for output in self._monitored_outputs:
            status[output] = 1 if output in output_states else 0

        _LOGGER.debug("Returning status: %s", status)

        if self._output_changed_callback:
            self._output_changed_callback(status)

    def _command_result(self, msg: SatelReadMessage):
        status = {"error": "Some problem!"}
        error_code = msg.msg_data[0]

        if error_code in [b"\x00", b"\xff"]:
            status = {"error": "OK"}
        elif error_code == b"\x01":
            status = {"error": "User code not found"}

        _LOGGER.debug("Received error status: %s", status)

    def _partitions_armed_state(self, mode: AlarmState, msg: SatelReadMessage):
        partitions = msg.get_active_bits(4)

        _LOGGER.debug("Update: list of partitions in mode %s: %s", mode, partitions)

        self.partition_states[mode] = partitions

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

        if enable_monitoring:
            self._start_task(self._monitor_reconnection_loop())
            await self.start_monitoring()

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

            data = SatelWriteMessage(
                SatelReadCommand.READ_DEVICE_NAME, raw_data=bytearray([0x01, 0x01])
            )
            _LOGGER.debug(
                "Keepalive sending after %.3fs of outbound inactivity", idle_for
            )
            connection_generation = self._connection.generation

            try:
                result = await self._send_data_and_wait(data)
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

                if msg.cmd in self._message_handlers:
                    _LOGGER.debug("Calling handler for command: %s", msg.cmd)
                    self._message_handlers[msg.cmd](msg)
                else:
                    _LOGGER.debug("No handler for command: %s", msg.cmd)

        except SatelConnectionStoppedError:
            return
        except asyncio.CancelledError:
            _LOGGER.info("_reading_loop loop cancelled.")
        except Exception as ex:
            _LOGGER.exception("Error in _reading_loop loop, %s", ex)

    async def _monitor_reconnection_loop(self):
        """Monitor for reconnection events and reinitialize monitoring.

        This task is only created when monitoring is enabled, so we can assume
        monitoring should be restarted on reconnection.
        """
        while True:
            try:
                # Wait indefinitely for a reconnection event
                await self._connection.wait_reconnected()
                _LOGGER.info("Connection re-established, reinitializing monitoring...")
                await self.start_monitoring()
            except SatelConnectionStoppedError:
                return
            except asyncio.CancelledError:
                break
            except Exception as ex:
                _LOGGER.exception("Error in _monitor_reconnection: %s", ex)
                await asyncio.sleep(1)

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

    def add_connection_status_callback(self, callback: ConnectionStateCallback) -> None:
        """Add a callback to be called when connection status changes."""
        self._connection.add_connection_state_callback(callback)

    # endregion

    # region Write actions
    async def arm(self, code, partition_list, mode=0):
        """Send arming command to the alarm. Modes allowed: from 0 till 3."""
        _LOGGER.debug("Sending arm command, mode: %s!", mode)

        mode_command = SatelWriteCommand(SatelWriteCommand.PARTITIONS_ARM_MODE_0 + mode)

        msg = SatelWriteMessage(mode_command, code=code, partitions=partition_list)

        await self._send_data(msg)

    async def disarm(self, code, partition_list):
        """Send command to disarm."""
        _LOGGER.info("Sending disarm command.")

        msg = SatelWriteMessage(
            SatelWriteCommand.PARTITIONS_DISARM, code=code, partitions=partition_list
        )

        await self._send_data(msg)

    async def clear_alarm(self, code, partition_list):
        """Send command to clear the alarm."""
        _LOGGER.info("Sending clear the alarm command.")

        msg = SatelWriteMessage(
            SatelWriteCommand.PARTITIONS_CLEAR_ALARM,
            code=code,
            partitions=partition_list,
        )

        await self._send_data(msg)

    async def set_output(self, code, output_id, state):
        """Send output turn on command to the alarm."""
        """0x88   outputs on
                  + 8 bytes - user code
                  + 16/32 bytes - output list
                  If function is accepted, function result can be
                  checked by observe the system state """
        _LOGGER.debug("Turn on, output: %s, code: %s", output_id, code)

        mode_command = (
            SatelWriteCommand.OUTPUTS_ON if state else SatelWriteCommand.OUTPUTS_OFF
        )
        msg = SatelWriteMessage(mode_command, code=code, zones_or_outputs=[output_id])
        await self._send_data(msg)

    async def read_temperature(self, zone_id: int) -> float | None:
        """Read the temperature for a single zone sensor."""
        request_zone_id = encode_zone_number(zone_id)
        msg = SatelWriteMessage(
            SatelReadCommand.ZONE_TEMPERATURE, raw_data=bytearray([request_zone_id])
        )
        response = await self._send_data_and_wait(msg)

        if response is None:
            _LOGGER.debug("No temperature response for zone %s", zone_id)
            return None

        if not isinstance(response, SatelZoneTemperatureReadMessage):
            msg = f"Unexpected response type for temperature read: {type(response).__name__}"
            raise SatelUnexpectedResponseError(msg)

        if response.zone_id != zone_id:
            msg = (
                "Temperature response zone mismatch: "
                f"expected {zone_id}, got {response.zone_id}"
            )
            raise ValueError(msg)

        return response.temperature

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
        request_zone_id = encode_zone_number(zone_id)
        msg = SatelWriteMessage(
            SatelReadCommand.READ_DEVICE_NAME,
            raw_data=bytearray(
                [SatelDeviceSelector.ZONE_WITH_PARTITION_ASSIGNMENT, request_zone_id]
            ),
        )
        response = await self._send_data_and_wait(msg)

        if response is None:
            _LOGGER.debug("No zone info response for zone %s", zone_id)
            return None

        if not isinstance(response, SatelZoneInfoReadMessage):
            msg = f"Unexpected response type for zone info read: {type(response).__name__}"
            raise SatelUnexpectedResponseError(msg)

        if response.device_info.number != zone_id:
            msg = (
                "Zone info response zone mismatch: "
                f"expected {zone_id}, got {response.device_info.number}"
            )
            raise ValueError(msg)

        return response.device_info

    async def read_panel_info(self) -> SatelPanelInfo | None:
        """Read structured panel information."""
        msg = SatelWriteMessage(SatelReadCommand.INTEGRA_VERSION)

        response = await self._send_data_and_wait(msg)
        if response is None:
            _LOGGER.warning("No panel info response received")
            return None

        if not isinstance(response, SatelIntegraVersionReadMessage):
            msg = (
                "Unexpected response type for INTEGRA version read: "
                f"{type(response).__name__}"
            )
            raise SatelUnexpectedResponseError(msg)

        return response.panel_info

    async def read_communication_module_info(
        self,
    ) -> SatelCommunicationModuleInfo | None:
        """Read structured communication module information."""
        msg = SatelWriteMessage(SatelReadCommand.MODULE_VERSION)

        response = await self._send_data_and_wait(msg)
        if response is None:
            _LOGGER.warning("No communication module info response received")
            return None

        if not isinstance(response, SatelModuleVersionReadMessage):
            msg = (
                "Unexpected response type for module version read: "
                f"{type(response).__name__}"
            )
            raise SatelUnexpectedResponseError(msg)

        return response.module_info

    # endregion

    # region Data management
    async def _send_data(self, msg: SatelWriteMessage) -> None:
        """Add message to the queue."""
        await self._queue.add_message(msg, False)

    async def _send_data_and_wait(self, msg: SatelWriteMessage):
        """Add message to the queue and wait for the result."""
        return await self._queue.add_message(msg, True)

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
            _LOGGER.debug("Received command: %s", msg)
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
