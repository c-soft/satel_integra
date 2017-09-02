# -*- coding: utf-8 -*-

"""Main module."""

from socket import *
import sys
import logging
from asyncio import IncompleteReadError

from enum import Enum, unique

_LOGGER = logging.getLogger(__name__)

# device types as defined by satel manual
PARTITION = b'\x00'
ZONE = b'\x01'
OUTPUT = b'\x04'


def checksum(command):
    """ Function to calculate a checksum as per Satel manual """

    crc = 0x147A
    for b in command:
        # rotate (crc 1 bit left)
        crc = ((crc << 1) & 0xFFFF) | (crc & 0x8000) >> 15
        crc = crc ^ 0xFFFF
        crc = (crc + (crc >> 8) + b) & 0xFFFF
    return crc


''' All logic is hidden here - this function will send the requests and extract the result
hence the poor man debugging inside
'''


def response_busy(resp):
    """Verifies if te response received indicated busy"""
    return resp[0:8] == b'\x10\x42\x75\x73\x79\x21\x0D\x0A'


def receive_data(sock):
    resp = sock.recv(100)
    _LOGGER.debug("-- Receving data --", file=sys.stderr)
    print_hex(resp)
    _LOGGER.debug("-- ------------- --", file=sys.stderr)
    return resp


def print_hex(data):
    hex_msg = ""
    for c in data: hex_msg += "\\x" + format(c, "02x")
    _LOGGER.debug(hex_msg)


def verify_and_strip(resp):
    if (resp[0:2] != b'\xFE\xFE'):
        _LOGGER.error("Houston, we got problem:")
        print_hex(resp)
        raise Exception("Wrong header - got %X%X" % (resp[0], resp[1]))
    if (resp[-2:] != b'\xFE\x0D'):
        raise Exception("Wrong footer - got %X%X" % (resp[-2], resp[-1]))
    output = resp[2:-2].replace(b'\xFE\xF0', b'\xFE')

    c = checksum(bytearray(output[0:-2]))

    if (256 * output[-2:-1][0] + output[-1:][0]) != c:
        raise Exception("Wrong checksum - got %d expected %d" % ((256 * output[-2:-1][0] + output[-1:][0]), c))

    return output[0:-2]


def list_set_bits(r, expected_length):
    set_bit_numbers = []
    bit_index = 0x1
    assert (len(r) == expected_length+1)

    for b in r[1:]:
        for i in range(8):
            if ((b >> i) & 1) == 1:
                set_bit_numbers.append(bit_index)
            bit_index += 1
    print("Bits set:")
    msg = ""
    for p in set_bit_numbers:
        msg += format(p, "#04X") + " "
    print(msg)
    return set_bit_numbers

def generate_query(command):
    data = bytearray(command)
    c = checksum(data)
    data.append(c >> 8)
    data.append(c & 0xFF)
    data.replace(b'\xFE', b'\xFE\xF0')

    data = bytearray.fromhex("FEFE") + data + bytearray.fromhex("FE0D")
    return data


################# CLASS #######################
@unique
class DeviceType(Enum):
    PARTITION = b'\x00'
    ZONE = b'\x01'
    USER = b'\x02'
    OUTPUT = b'\x04'


@unique
class AlarmState(Enum):
    DISARMED = 0x0
    ARMED_MODE0 = 0x0A
    ARMED_MODE1 = 0x2A
    ARMED_MODE2 = 0x0B
    ARMED_MODE3 = 0x0C


@unique
class ArmingResult(Enum):
    OK = 0x0
    USER_CODE_NOT_FOUND = 0x01
    CANT_ARM_USE_FORCE_ARM = 0x11
    CANT_ARM = 0x12

import asyncio

class AlarmStatusUpdate():
    def __init__(self, host, port, monitored_zones, loop):
        """Init the Satel alarm panel."""
        self._host = host

class AsyncSatel():
    def __init__(self, host, port, monitored_zones, loop):
        """Init the Satel alarm panel."""
        self._host = host
        self._port = port
        self._loop = loop
        self._message_handlers = {}
        self._monitored_zones = monitored_zones
        self._keep_alive_timeout = 10
        self._reader = None
        self._writer = None
        # self._update_commands = {
        #     b'\x00': ("zones violation", 16, self.zone_violation ),
        #     b'\x0A': ("armed partitions (really)", 4, lambda msg: self.armed(1,msg)),
        #     b'\x0B': ("partitions armed in mode 2", 4, lambda msg: self.armed(2,msg)),
        #     b'\x0C': ("partitions armed in mode 3", 4, lambda msg: self.armed(3,msg)),
        #     b'\x13': ("partitions alarm", 4, lambda msg: self.alarm(3,msg)),
        #     b'\x14': ("partitions fire alarm", 4, lambda msg: self.alarm(3,True,msg)),
        # }
        # Assign handler
        #self._message_handlers[b'\x00'] = self._update_commands[b'\x00'][2]
        self._message_handlers[b'\x00'] = self._zone_violated
        self._message_handlers[b'\x0A'] = lambda msg: self._armed(0, msg)
        self._message_handlers[b'\x2A'] = lambda msg: self._armed(1, msg)
        self._message_handlers[b'\x0B'] = lambda msg: self._armed(2, msg)
        self._message_handlers[b'\x0C'] = lambda msg: self._armed(3, msg)
        self._message_handlers[b'\xEF'] = self._error_occured
        self._message_handlers[b'\x13'] = lambda msg: self._alarm_triggered(msg)
        self._message_handlers[b'\x14'] = lambda msg: self._alarm_triggered(msg,"fire")


    @property
    def connected(self):
        return self._writer and self._reader

    @asyncio.coroutine
    def connect(self):
        self._reader, self._writer = yield from asyncio.open_connection(self._host, self._port,loop=self._loop)
        yield from self._start_monitoring()
        return True

    @asyncio.coroutine
    def _start_monitoring(self):

        data = generate_query(b'\x7F\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00')

        yield from self._send_data(data)
        resp = yield from self._read_data()

        if resp[1:2] != b'\xFF':
            raise Exception("Monitoring not accepted.")

    def _zone_violated(self, msg):

        status = {"zones":{}}

        violated_zones = list_set_bits(msg, 32)
        _LOGGER.debug("Violated zones: %s, monitored zones: %s", violated_zones, self._monitored_zones )
        for zone in self._monitored_zones:
            status["zones"][zone] = \
                1 if zone in violated_zones else 0

        _LOGGER.debug("Returning status: %s",status)
        return status

    def _error_occured(self, msg):
        status = {"error": "Some problem!"}
        error_code = msg[1:2]

        if error_code in [b'\x00',b'\xFF']:
            status = {"error": "OK"}
        elif error_code == b'\x01':
            status = {"error": "User code not found"}

        _LOGGER.debug("Received error status: %s", status )
        return status

    @asyncio.coroutine
    def _send_data(self, data):
        _LOGGER.debug("-- Sending data --")
        print_hex(data)
        _LOGGER.debug("-- ------------- --")
        _LOGGER.debug("Sent %d bytes",len(data))

        self._writer.write(data)
        yield from self._writer.drain()

    @asyncio.coroutine
    def arm(self, code):
        while len(code) < 16:
            code += 'F'

        code_bytes = bytearray.fromhex(code)

        data = generate_query(b'\x80' + code_bytes + b'\x01\x00\x00\x00')

        yield from self._send_data(data)

    def _armed(self, mode, msg):
        _LOGGER.debug("Alarm update!")
        status = {"alarm_status": "armed", "mode": mode }
        return status

    def _alarm_triggered(self, msg, type="violation"):
        _LOGGER.debug("Alarm triggered, type: %s", type)
        status = {"alarm_status": "ringing", "type": type}
        return status

    def _read_data(self):
        #data = yield from self._reader.read(100)
        data = yield from self._reader.readuntil(b'\xFE\x0D')
        _LOGGER.debug("-- Receving data --")
        print_hex(data)
        _LOGGER.debug("-- ------------- --")
        return verify_and_strip(data)

    @asyncio.coroutine
    def keep_alive(self):
        """This method is a workaround for Satel Integra disconnecting after 25s.

        Every interval it sends some random question to the device, ignoring answer - just to keep connection alive.
        """
        while True:
            yield from asyncio.sleep(self._keep_alive_timeout)
            data = generate_query(b'\xEE\x01\x01')
            yield from self._send_data(data)

    def get_status(self):
        _LOGGER.debug("Wait...")

        try:
            resp = yield from self._read_data()
        except IncompleteReadError as e:
            _LOGGER.warning("Got exception: %s. Most likely the other side has disconnected!", e)
            self._writer = None
            self._reader = None
            return {"connected": False}

        if not resp:
            _LOGGER.warning("Got empty response. We think it's disconnect.")
            self._writer = None
            self._reader = None
            return {"connected": False}

        id = resp[0:1]
        if id in self._message_handlers:
            _LOGGER.info("Calling handler for id: %s", id)
            return self._message_handlers[id](resp)
        else:
            _LOGGER.info("Ignoring message: %s", id )
            return None

    def close(self):
        _LOGGER.debug("Closing...")
        if self.connected:
            self._writer.close()

def demo2(host, port):
    logging.basicConfig(level=logging.DEBUG)

    loop = asyncio.get_event_loop()
    stl = AsyncSatel(host,
                     port,
                     [1,2,3,4,5,6,7,8,12,13,14,15,16,17,18,19,20,21,22,23,25,26,27,28,29,30],
                     loop)

    @asyncio.coroutine
    def update_satel_status():
        while (True):
            if not stl.connected:
                yield from stl.connect()
            while (True):
                status = yield from stl.get_status()
                if status and "connected" in status:
                    break

    #loop.run_until_complete(asyncio.ensure_future(update_satel_status()))
    loop.run_until_complete(stl.connect())
    loop.create_task(stl.arm("3333"))
    #loop.create_task(stl.disarm("3333"))
    loop.create_task(stl.keep_alive())
    loop.create_task(update_satel_status())

    loop.run_forever()
    loop.close()
