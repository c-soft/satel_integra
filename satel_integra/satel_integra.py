# """Main module."""

import asyncio
import logging
from enum import Enum, unique
from collections.abc import Callable

from satel_integra.commands import SatelReadCommand, SatelWriteCommand
from satel_integra.connection import SatelConnection
from satel_integra.messages import SatelReadMessage, SatelWriteMessage
from satel_integra.utils import encode_bitmask_le
from satel_integra.queue import SatelMessageQueue

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
        self._reading_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._keepalive_timeout = 20

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
            SatelReadCommand.PARTITIONS_ARMED_SUPPRESSED: lambda msg: self._partitions_armed_state(
                AlarmState.ARMED_SUPPRESSED, msg
            ),
            SatelReadCommand.PARTITIONS_ARMED_MODE0: lambda msg: self._partitions_armed_state(
                AlarmState.ARMED_MODE0, msg
            ),
            SatelReadCommand.PARTITIONS_ARMED_MODE2: lambda msg: self._partitions_armed_state(
                AlarmState.ARMED_MODE2, msg
            ),
            SatelReadCommand.PARTITIONS_ARMED_MODE3: lambda msg: self._partitions_armed_state(
                AlarmState.ARMED_MODE3, msg
            ),
            SatelReadCommand.PARTITIONS_ENTRY_TIME: lambda msg: self._partitions_armed_state(
                AlarmState.ENTRY_TIME, msg
            ),
            SatelReadCommand.PARTITIONS_EXIT_COUNTDOWN_OVER_10: lambda msg: self._partitions_armed_state(
                AlarmState.EXIT_COUNTDOWN_OVER_10, msg
            ),
            SatelReadCommand.PARTITIONS_EXIT_COUNTDOWN_UNDER_10: lambda msg: self._partitions_armed_state(
                AlarmState.EXIT_COUNTDOWN_UNDER_10, msg
            ),
            SatelReadCommand.PARTITIONS_ALARM: lambda msg: self._partitions_armed_state(
                AlarmState.TRIGGERED, msg
            ),
            SatelReadCommand.PARTITIONS_FIRE_ALARM: lambda msg: self._partitions_armed_state(
                AlarmState.TRIGGERED_FIRE, msg
            ),
            SatelReadCommand.OUTPUTS_STATE: self._outputs_changed,
            SatelReadCommand.PARTITIONS_ARMED_MODE1: lambda msg: self._partitions_armed_state(
                AlarmState.ARMED_MODE1, msg
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
        await self._connection.ensure_connected()

        await self._queue.start()

        # Start background loops
        if not self._reading_task or self._reading_task.done():
            self._reading_task = asyncio.create_task(self._reading_loop())

        if not self._keepalive_task or self._keepalive_task.done():
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())

        if enable_monitoring:
            await self.start_monitoring()

    async def _keepalive_loop(self):
        """A workaround for Satel Integra disconnecting after 25s.

        Every interval it sends some random question to the device, ignoring
        answer - just to keep connection alive.
        """
        while True:
            await asyncio.sleep(self._keepalive_timeout)
            if self.closed:
                return
            # Command to read status of the alarm
            data = SatelWriteMessage(
                SatelWriteCommand.READ_DEVICE_NAME, raw_data=bytearray([0x01, 0x01])
            )
            await self._send_data(data)

    async def _reading_loop(self):
        try:
            while not self.closed:
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

        except asyncio.CancelledError:
            _LOGGER.info("_reading_loop loop cancelled.")
        except Exception as ex:
            _LOGGER.exception("Error in _reading_loop loop, %s", ex)

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
    def closed(self) -> bool:
        """Return true if connection is closed."""
        return self._connection.closed

    async def connect(self) -> bool:
        """Make a TCP connection to the alarm system."""
        result = await self._connection.connect()

        return result

    async def close(self):
        """Stop monitoring and close connection."""
        await self._queue.stop()

        if self._reading_task:
            self._reading_task.cancel()
            try:
                await self._reading_task
            except asyncio.CancelledError:
                pass
            self._reading_task = None

        if self._keepalive_task:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
            self._keepalive_task = None

        await self._connection.close()

    # endregion
