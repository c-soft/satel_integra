# -*- coding: utf-8 -*-

"""Main module."""

import asyncio
import logging
from enum import Enum, unique

_LOGGER = logging.getLogger(__name__)


def checksum(command):
    """Function to calculate checksum as per Satel manual."""
    crc = 0x147A
    for b in command:
        # rotate (crc 1 bit left)
        crc = ((crc << 1) & 0xFFFF) | (crc & 0x8000) >> 15
        crc = crc ^ 0xFFFF
        crc = (crc + (crc >> 8) + b) & 0xFFFF
    return crc


def print_hex(data):
    """Debugging method to print out frames in hex."""
    hex_msg = ""
    for c in data:
        hex_msg += "\\x" + format(c, "02x")
    _LOGGER.debug(hex_msg)


def verify_and_strip(resp):
    """Verify checksum and strip header and footer of received frame."""
    if resp[0:2] != b'\xFE\xFE':
        _LOGGER.error("Houston, we got problem:")
        print_hex(resp)
        raise Exception("Wrong header - got %X%X" % (resp[0], resp[1]))
    if resp[-2:] != b'\xFE\x0D':
        raise Exception("Wrong footer - got %X%X" % (resp[-2], resp[-1]))
    output = resp[2:-2].replace(b'\xFE\xF0', b'\xFE')

    c = checksum(bytearray(output[0:-2]))

    if (256 * output[-2:-1][0] + output[-1:][0]) != c:
        raise Exception("Wrong checksum - got %d expected %d" % (
            (256 * output[-2:-1][0] + output[-1:][0]), c))

    return output[0:-2]


def list_set_bits(r, expected_length):
    """Return list of positions of bits set to one in given data.

    This method is used to read e.g. violated zones. They are marked by ones
    on respective bit positions - as per Satel manual.
    """
    set_bit_numbers = []
    bit_index = 0x1
    assert (len(r) == expected_length + 1)

    for b in r[1:]:
        for i in range(8):
            if ((b >> i) & 1) == 1:
                set_bit_numbers.append(bit_index)
            bit_index += 1

    return set_bit_numbers


def generate_query(command):
    """Add header, checksum and footer to command data."""
    data = bytearray(command)
    c = checksum(data)
    data.append(c >> 8)
    data.append(c & 0xFF)
    data.replace(b'\xFE', b'\xFE\xF0')

    data = bytearray.fromhex("FEFE") + data + bytearray.fromhex("FE0D")
    return data


def output_bytes(output):
    _LOGGER.debug("output_bytes")
    output_no = 1 << output - 1
    return output_no.to_bytes(32, 'little')


def partition_bytes(partition_list):
        ret_val = 0
        for position in partition_list:
            if position >= 32:
                raise IndexError()
            ret_val = ret_val | (1 << (position - 1))

        return ret_val.to_bytes(4, 'little')


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

    def __init__(self, host, port, loop, monitored_zones=[],
                 monitored_outputs=[], partitions=[]):
        """Init the Satel alarm data."""
        self._host = host
        self._port = port
        self._loop = loop
        self._message_handlers = {}
        self._monitored_zones = monitored_zones
        self.violated_zones = []
        self._monitored_outputs = monitored_outputs
        self.violated_outputs = []
        self.partition_states = {}
        self._keep_alive_timeout = 20
        self._reconnection_timeout = 15
        self._reader = None
        self._writer = None
        self.closed = False
        self._alarm_status_callback = None
        self._zone_changed_callback = None
        self._output_changed_callback = None
        self._partitions = partitions
        self._command_status_event = asyncio.Event()
        self._command_status = False

        self._message_handlers[b'\x00'] = self._zone_violated
        self._message_handlers[b'\x17'] = self._output_changed
        self._message_handlers[b'\x0A'] = lambda msg: self._armed(
            AlarmState.ARMED_MODE0, msg)
        self._message_handlers[b'\x2A'] = lambda msg: self._armed(
            AlarmState.ARMED_MODE1, msg)
        self._message_handlers[b'\x0B'] = lambda msg: self._armed(
            AlarmState.ARMED_MODE2, msg)
        self._message_handlers[b'\x0C'] = lambda msg: self._armed(
            AlarmState.ARMED_MODE3, msg)
        self._message_handlers[b'\x09'] = lambda msg: self._armed(
            AlarmState.ARMED_SUPPRESSED, msg)
        self._message_handlers[b'\x0E'] = lambda msg: self._armed(
            AlarmState.ENTRY_TIME, msg)
        self._message_handlers[b'\x0F'] = lambda msg: self._armed(
            AlarmState.EXIT_COUNTDOWN_OVER_10, msg)
        self._message_handlers[b'\x10'] = lambda msg: self._armed(
            AlarmState.EXIT_COUNTDOWN_UNDER_10, msg)
        self._message_handlers[b'\xEF'] = lambda msg: self._command_result(msg)
        self._message_handlers[b'\x13'] = lambda msg: self._armed(
            AlarmState.TRIGGERED, msg)
        self._message_handlers[b'\x14'] = lambda msg: self._armed(
            AlarmState.TRIGGERED_FIRE, msg)

    @property
    def connected(self):
        """Return true if there is connection to the alarm."""
        return self._writer and self._reader

    async def connect(self):
        """Make a TCP connection to the alarm system."""
        _LOGGER.debug("Connecting...")

        try:
            self._reader, self._writer = await asyncio.open_connection(
                self._host, self._port)
            _LOGGER.debug("sucess connecting...")

        except Exception as e:
            _LOGGER.warning(
                "Exception during connecting: %s.", e)
            self._writer = None
            self._reader = None
            return False

        return True

    async def start_monitoring(self):
        """Start monitoring for interesting events."""
        data = generate_query(
            b'\x7F\x01\xDC\x99\x80\x00\x04\x00\x00\x00\x00\x00\x00')

        await self._send_data(data)
        resp = await self._read_data()

        if resp is None:
            _LOGGER.warning("Start monitoring - no data!")
            return

        if resp[1:2] != b'\xFF':
            _LOGGER.warning("Monitoring not accepted.")

    def _zone_violated(self, msg):

        status = {"zones": {}}

        violated_zones = list_set_bits(msg, 32)
        self.violated_zones = violated_zones
        _LOGGER.debug("Violated zones: %s", violated_zones)
        for zone in self._monitored_zones:
            status["zones"][zone] = \
                1 if zone in violated_zones else 0

        _LOGGER.debug("Returning status: %s", status)

        if self._zone_changed_callback:
            self._zone_changed_callback(status)

        return status

    def _output_changed(self, msg):
        """0x17   outputs state 0x17   + 16/32 bytes"""

        status = {"outputs": {}}

        output_states = list_set_bits(msg, 32)
        self.violated_outputs = output_states
        _LOGGER.debug("Output states: %s, monitored outputs: %s",
                      output_states, self._monitored_outputs)
        for output in self._monitored_outputs:
            status["outputs"][output] = \
                1 if output in output_states else 0

        _LOGGER.debug("Returning status: %s", status)

        if self._output_changed_callback:
            self._output_changed_callback(status)

        return status

    def _command_result(self, msg):
        status = {"error": "Some problem!"}
        error_code = msg[1:2]

        if error_code in [b'\x00', b'\xFF']:
            status = {"error": "OK"}
        elif error_code == b'\x01':
            status = {"error": "User code not found"}

        _LOGGER.debug("Received error status: %s", status)
        self._command_status = status
        self._command_status_event.set()
        return status

    # async def send_and_wait_for_answer(self, data):
    #     """Send given data and wait for confirmation from Satel"""
    #     await self._send_data(data)
    #     try:
    #         await asyncio.wait_for(self._command_status_event.wait(),
    #                                timeout=5)
    #     except asyncio.TimeoutError:
    #         _LOGGER.warning("Timeout waiting for reponse from Satel!")
    #     return self._command_status

    async def _send_data(self, data):
        _LOGGER.debug("-- Sending data --")
        print_hex(data)
        _LOGGER.debug("-- ------------- --")
        _LOGGER.debug("Sending %d bytes...", len(data))

        if not self._writer:
            _LOGGER.warning("Ignoring data because we're disconnected!")
            return
        try:
            self._writer.write(data)
            await self._writer.drain()
        except Exception as e:
            _LOGGER.warning(
                "Exception during sending data: %s.", e)
            self._writer = None
            self._reader = None
            return False

    async def arm(self, code, partition_list, mode=0):
        """Send arming command to the alarm. Modes allowed: from 0 till 3."""
        _LOGGER.debug("Sending arm command, mode: %s!", mode)
        while len(code) < 16:
            code += 'F'

        code_bytes = bytearray.fromhex(code)
        mode_command = 0x80 + mode
        data = generate_query(mode_command.to_bytes(1, 'big')
                              + code_bytes
                              + partition_bytes(partition_list))

        await self._send_data(data)

    async def disarm(self, code, partition_list):
        """Send command to disarm."""
        _LOGGER.info("Sending disarm command.")
        while len(code) < 16:
            code += 'F'

        code_bytes = bytearray.fromhex(code)

        data = generate_query(b'\x84' + code_bytes
                              + partition_bytes(partition_list))

        await self._send_data(data)

    async def clear_alarm(self, code, partition_list):
        """Send command to clear the alarm."""
        _LOGGER.info("Sending clear the alarm command.")
        while len(code) < 16:
            code += 'F'

        code_bytes = bytearray.fromhex(code)

        data = generate_query(b'\x85' + code_bytes
                              + partition_bytes(partition_list))

        await self._send_data(data)

    async def set_output(self, code, output_id, state):
        """Send output turn on command to the alarm."""
        """0x88   outputs on
              + 8 bytes - user code
              + 16/32 bytes - output list
              If function is accepted, function result can be
              checked by observe the system state """
        _LOGGER.debug("Turn on, output: %s, code: %s", output_id, code)
        while len(code) < 16:
            code += 'F'

        code_bytes = bytearray.fromhex(code)
        mode_command = 0x88 if state else 0x89
        data = generate_query(mode_command.to_bytes(1, 'big') +
                              code_bytes +
                              output_bytes(output_id))
        await self._send_data(data)

    def _armed(self, mode, msg):
        partitions = list_set_bits(msg, 4)

        _LOGGER.debug("Update: list of partitions in mode %s: %s",
                      mode, partitions)

        self.partition_states[mode] = partitions

        if self._alarm_status_callback:
            self._alarm_status_callback()

    async def _read_data(self):
        if not self._reader:
            return []

        try:
            data = await self._reader.readuntil(b'\xFE\x0D')
            _LOGGER.debug("-- Receiving data --")
            print_hex(data)
            _LOGGER.debug("-- ------------- --")
            return verify_and_strip(data)

        except Exception as e:
            _LOGGER.warning(
                "Got exception: %s. Most likely the other side has "
                "disconnected!", e)
            self._writer = None
            self._reader = None

            if self._alarm_status_callback:
                self._alarm_status_callback()

    async def keep_alive(self):
        """A workaround for Satel Integra disconnecting after 25s.

        Every interval it sends some random question to the device, ignoring
        answer - just to keep connection alive.
        """
        while True:
            await asyncio.sleep(self._keep_alive_timeout)
            if self.closed:
                return
            # Command to read status of the alarm
            data = generate_query(b'\xEE\x01\x01')
            await self._send_data(data)

    async def _update_status(self):
        _LOGGER.debug("Wait...")

        resp = await self._read_data()

        if not resp:
            _LOGGER.warning("Got empty response. We think it's disconnect.")
            self._writer = None
            self._reader = None
            if self._alarm_status_callback:
                self._alarm_status_callback()
            return

        msg_id = resp[0:1]
        str_msg_id = ''.join(format(x, '02x') for x in msg_id)
        if msg_id in self._message_handlers:
            _LOGGER.info("Calling handler for id: 0x%s", str_msg_id)
            self._message_handlers[msg_id](resp)
        else:
            _LOGGER.info("Ignoring message: 0x%s", str_msg_id)

    async def monitor_status(self, alarm_status_callback=None,
                             zone_changed_callback=None,
                             output_changed_callback=None):
        """Start monitoring of the alarm status.

        Send command to satel integra to start sending updates. Read in a
        loop and call respective callbacks when received messages.
        """
        self._alarm_status_callback = alarm_status_callback
        self._zone_changed_callback = zone_changed_callback
        self._output_changed_callback = output_changed_callback

        _LOGGER.info("Starting monitor_status loop")

        while not self.closed:
            _LOGGER.debug("Iteration... ")
            while not self.connected:
                _LOGGER.info("Not connected, re-connecting... ")
                await self.connect()
                if not self.connected:
                    _LOGGER.warning("Not connected, sleeping for 10s... ")
                    await asyncio.sleep(self._reconnection_timeout)
                    continue
            await self.start_monitoring()
            if not self.connected:
                _LOGGER.warning("Start monitoring failed, sleeping for 10s...")
                await asyncio.sleep(self._reconnection_timeout)
                continue
            while True:
                await self._update_status()
                _LOGGER.debug("Got status!")
                if not self.connected:
                    _LOGGER.info("Got connection broken, reconnecting!")
                    break
        _LOGGER.info("Closed, quit monitoring.")

    def close(self):
        """Stop monitoring and close connection."""
        _LOGGER.debug("Closing...")
        self.closed = True
        if self.connected:
            self._writer.close()


def demo(host, port):
    """Basic demo of the monitoring capabilities."""
    # logging.basicConfig(level=logging.DEBUG)

    loop = asyncio.get_event_loop()
    stl = AsyncSatel(host,
                     port,
                     loop,
                     [1, 2, 3, 4, 5, 6, 7, 8, 12, 13, 14, 15, 16, 17, 18, 19,
                      20, 21, 22, 23, 25, 26, 27, 28, 29, 30],
                     [8, 9, 10]
                     )

    loop.run_until_complete(stl.connect())
    loop.create_task(stl.arm("3333", (1,)))
    loop.create_task(stl.disarm("3333",(1,)))
    loop.create_task(stl.keep_alive())
    loop.create_task(stl.monitor_status())

    loop.run_forever()
    loop.close()
