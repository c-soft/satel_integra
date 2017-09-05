# -*- coding: utf-8 -*-

"""Main module."""

import asyncio
import logging
from asyncio import IncompleteReadError
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
    on respective bit positions - as per satel manual.
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


@unique
class AlarmState(Enum):
    """Represents status of the alarm."""

    ARMED_MODE0 = 0
    ARMED_MODE1 = 1
    ARMED_MODE2 = 2
    ARMED_MODE3 = 3
    TRIGGERED = 4
    TRIGGERED_FIRE = 5
    DISARMED = 6
    DISCONNECTED = 7


class AsyncSatel:
    """Asynchronous interface to talk to Satel Integra alarm system."""

    def __init__(self, host, port, monitored_zones, loop, partition_id=1):
        """Init the Satel alarm data."""
        self._host = host
        self._port = port
        self._loop = loop
        self._message_handlers = {}
        self._monitored_zones = monitored_zones
        self._keep_alive_timeout = 10
        self._reader = None
        self._writer = None
        self.closed = False
        self._alarm_status_callback = None
        self._zone_changed_callback = None
        self._partition_id = partition_id
        self._state = AlarmState.DISCONNECTED

        # self._update_commands = {
        #     b'\x00': ("zones violation", 16, self.zone_violation ),
        #     b'\x0A': ("armed partitions (really)", 4, lambda msg:
        # self.armed(1,msg)),
        #     b'\x0B': ("partitions armed in mode 2", 4, lambda msg:
        # self.armed(2,msg)),
        #     b'\x0C': ("partitions armed in mode 3", 4, lambda msg:
        # self.armed(3,msg)),
        #     b'\x13': ("partitions alarm", 4, lambda msg: self.alarm(3,msg)),
        #     b'\x14': ("partitions fire alarm", 4, lambda msg: self.alarm(
        # 3,True,msg)),
        # }
        # Assign handler
        # self._message_handlers[b'\x00'] = self._update_commands[b'\x00'][2]
        self._message_handlers[b'\x00'] = self._zone_violated
        self._message_handlers[b'\x0A'] = lambda msg: self._armed(
            AlarmState.ARMED_MODE0, msg)
        self._message_handlers[b'\x2A'] = lambda msg: self._armed(
            AlarmState.ARMED_MODE1, msg)
        self._message_handlers[b'\x0B'] = lambda msg: self._armed(
            AlarmState.ARMED_MODE2, msg)
        self._message_handlers[b'\x0C'] = lambda msg: self._armed(
            AlarmState.ARMED_MODE3, msg)
        self._message_handlers[b'\xEF'] = self._error_occurred
        self._message_handlers[b'\x13'] = lambda msg: self._armed(
            AlarmState.TRIGGERED, msg)
        self._message_handlers[b'\x14'] = lambda msg: self._armed(
            AlarmState.TRIGGERED_FIRE, msg)

    @property
    def connected(self):
        """Return true if there is connection to the alarm."""
        return self._writer and self._reader

    @asyncio.coroutine
    def connect(self):
        """Make a TCP connection to the alarm system."""
        self._reader, self._writer = yield from asyncio.open_connection(
            self._host, self._port, loop=self._loop)
        return True

    @asyncio.coroutine
    def start_monitoring(self):
        """Start monitoring for interesting events."""
        data = generate_query(
            b'\x7F\x01\x0E\x08\x00\x00\x04\x00\x00\x00\x00\x00\x00')

        yield from self._send_data(data)
        resp = yield from self._read_data()

        if resp[1:2] != b'\xFF':
            raise Exception("Monitoring not accepted.")

    def _zone_violated(self, msg):

        status = {"zones": {}}

        violated_zones = list_set_bits(msg, 32)
        _LOGGER.debug("Violated zones: %s, monitored zones: %s",
                      violated_zones, self._monitored_zones)
        for zone in self._monitored_zones:
            status["zones"][zone] = \
                1 if zone in violated_zones else 0

        _LOGGER.debug("Returning status: %s", status)

        if self._zone_changed_callback:
            self._zone_changed_callback(status)

        return status

    @staticmethod
    def _error_occurred(msg):
        status = {"error": "Some problem!"}
        error_code = msg[1:2]

        if error_code in [b'\x00', b'\xFF']:
            status = {"error": "OK"}
        elif error_code == b'\x01':
            status = {"error": "User code not found"}

        _LOGGER.debug("Received error status: %s", status)
        return status

    @asyncio.coroutine
    def _send_data(self, data):
        _LOGGER.debug("-- Sending data --")
        print_hex(data)
        _LOGGER.debug("-- ------------- --")
        _LOGGER.debug("Sent %d bytes", len(data))

        self._writer.write(data)
        yield from self._writer.drain()

    @property
    def _partition_bytes(self):
        partition = 1 << self._partition_id - 1
        return partition.to_bytes(4, 'little')

    @asyncio.coroutine
    def arm(self, code, mode=0):
        """Send arming command to the alarm. Modes allowed: from 0 till 3."""
        _LOGGER.debug("Alarm arm, mode: %s!", mode)
        while len(code) < 16:
            code += 'F'

        code_bytes = bytearray.fromhex(code)
        mode_command = 0x80 + mode
        data = generate_query(mode_command.to_bytes(1, 'big') +
                              code_bytes +
                              self._partition_bytes)

        yield from self._send_data(data)

    @asyncio.coroutine
    def disarm(self, code):
        """Send command to disarm."""
        _LOGGER.debug("Alarm disarm, code: %s")
        while len(code) < 16:
            code += 'F'

        code_bytes = bytearray.fromhex(code)

        data = generate_query(b'\x84' + code_bytes + self._partition_bytes)

        yield from self._send_data(data)

    def _armed(self, mode, msg):
        _LOGGER.debug("Alarm update, mode: %s", mode)

        partitions = list_set_bits(msg, 4)

        if self._state in [mode, AlarmState.DISCONNECTED]:
            if self._partition_id not in partitions:
                self._state = AlarmState.DISARMED
        else:
            if self._partition_id in partitions:
                self._state = AlarmState(mode)

        if self._alarm_status_callback:
            self._alarm_status_callback(self._state)

        return self._state

    def _read_data(self):
        # data = yield from self._reader.read(100)
        data = yield from self._reader.readuntil(b'\xFE\x0D')
        _LOGGER.debug("-- Receiving data --")
        print_hex(data)
        _LOGGER.debug("-- ------------- --")
        return verify_and_strip(data)

    @asyncio.coroutine
    def keep_alive(self):
        """A workaround for Satel Integra disconnecting after 25s.

        Every interval it sends some random question to the device, ignoring
        answer - just to keep connection alive.
        """
        while True:
            yield from asyncio.sleep(self._keep_alive_timeout)
            if self.closed:
                return
            data = generate_query(b'\xEE\x01\x01')
            yield from self._send_data(data)

    def _update_status(self):
        _LOGGER.debug("Wait...")

        try:
            resp = yield from self._read_data()
        except IncompleteReadError as e:
            _LOGGER.warning(
                "Got exception: %s. Most likely the other side has "
                "disconnected!", e)
            self._writer = None
            self._reader = None
            self._state = AlarmState.DISCONNECTED
            return self._state

        if not resp:
            _LOGGER.warning("Got empty response. We think it's disconnect.")
            self._writer = None
            self._reader = None
            self._state = AlarmState.DISCONNECTED
            return self._state

        msg_id = resp[0:1]
        if msg_id in self._message_handlers:
            _LOGGER.info("Calling handler for id: %s", msg_id)
            return self._message_handlers[msg_id](resp)
        else:
            _LOGGER.info("Ignoring message: %s", msg_id)
            return None

    @asyncio.coroutine
    def monitor_status(self, alarm_status_callback=None,
                       zone_changed_callback=None):
        """Start monitoring of the alarm status.

        Send command to satel integra to start sending updates. Read in a
        loop and call respective callbacks when received messages.
        """
        self._alarm_status_callback = alarm_status_callback
        self._zone_changed_callback = zone_changed_callback

        _LOGGER.info("Starting monitor_status loop")
        yield from self.start_monitoring()

        while not self.closed:
            _LOGGER.debug("Iteration... ")
            if not self.connected:
                _LOGGER.info("Not connected, re-connecting... ")
                yield from self.connect()
                yield from self.start_monitoring()

            while True:
                status = yield from self._update_status()
                _LOGGER.debug("Got status!")
                if status and status == AlarmState.DISCONNECTED:
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
                     [1, 2, 3, 4, 5, 6, 7, 8, 12, 13, 14, 15, 16, 17, 18, 19,
                      20, 21, 22, 23, 25, 26, 27, 28, 29, 30],
                     loop)

    loop.run_until_complete(stl.connect())
    loop.create_task(stl.arm("3333", 1))
    loop.create_task(stl.disarm("3333"))
    loop.create_task(stl.keep_alive())
    loop.create_task(stl.monitor_status())

    loop.run_forever()
    loop.close()
