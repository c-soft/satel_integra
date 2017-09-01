# -*- coding: utf-8 -*-

"""Main module."""

from socket import *
import time
import sys
import logging


from enum import Enum, unique

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.DEBUG)

# Extra debugging
DEBUG = 1

# Delay between consecutive commands
DELAY = 0.002


encoding = "cp1250"

# Maximum number of retries when talking to Integra
MAX_ATTEMPTS = 3

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
    if DEBUG:
        _LOGGER.debug("-- Receving data --", file=sys.stderr)
        print_hex(resp)
        _LOGGER.debug("-- ------------- --", file=sys.stderr)
    return resp


def print_hex(data):
    hex_msg = ""
    for c in data: hex_msg += "\\x" + format(c, "02x")
    _LOGGER.info(hex_msg)


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


''' Returns a string that can be sent to enable/disable a given output
'''


def outputAsString(output):
    string = ""
    byte = output // 8 + 1
    while byte > 1:
        string += "00"
        byte -= 1
    out = 1 << (output % 8 - 1)
    result = str(hex(out)[2:])
    if len(result) == 1:
        result = "0" + result
    string += result
    while len(string) < 32:
        string += "0"
    return string

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


class SatelEthm():
    """Class wrapping the integration protocol to the Satel ETHM network module."""

    def __init__(self, socket):
        """Init the Satel alarm panel."""
        self._status = AlarmState.DISARMED
        self._partitions = {}
        #self._host = host
        #self._port = port
        self._socket = socket

        # Dictionary: command => ( name of the information, how many bytes in an answer)
        self._update_commands = [
            (b'\x00', "zones violation", 16),
            (b'\x01', "zones tamper", 16),
            (b'\x02', "zones alarm", 16),
            (b'\x03', "zones tamper alarm", 16),
            (b'\x04', "zones alarm memory", 16),
            (b'\x05', "zones tamper alarm memory", 16),
            (b'\x06', "zones bypass", 16),
            (b'\x07', "zones 'no violation' trouble", 16),
            (b'\x08', "zones 'long violation' trouble", 16),
            (b'\x09', "armed partitions (suppressed)", 4),
            (b'\x0A', "armed partitions (really)", 4),
            (b'\x0B', "partitions armed in mode 2", 4),
            (b'\x0C', "partitions armed in mode 3", 4),
            (b'\x0D', "partitions with 1st code entered", 4),
            (b'\x0E', "partitions entry time", 4),
            (b'\x0F', "partitions exit time >10s", 4),
            (b'\x10', "partitions exit time <10s", 4),
            (b'\x11', "partitions temporary blocked", 4),
            (b'\x12', "partitions blocked for guard round", 4),
            (b'\x13', "partitions alarm", 4),
            (b'\x14', "partitions fire alarm", 4),
            (b'\x15', "partitions alarm memory", 4),
            (b'\x16', "partitions fire alarm memory", 4),
            (b'\x17', "outputs state", 16),
            (b'\x18', "doors opened", 8),
            (b'\x19', "doors opened long", 8),
        ]

        self._device_types = [
            (b'\x00', "partition", 32),
            (b'\x01', "zone", 128),
            (b'\x02', "user", 255),
        ]

        self._current_status = {}
        self._device_name_cache = {}

#    def connect(self):
#        self._socket = socket(AF_INET, SOCK_STREAM)
#        self._socket.connect((self._host,self._port))

    @staticmethod
    def _hardware_model(code):
        model_strings = {
            0: "24",
            1: "32",
            2: "64",
            3: "128",
            4: "128-WRL SIM300",
            132: "128-WRL LEON",
            66: "64 PLUS",
            67: "128 PLUS",
            72: "256 PLUS"
        }
        if code not in model_strings:
            return "UNKNOWN"
        else:
            return "INTEGRA " + model_strings[code]

    def send_command(self, command):

        data = generate_query(command)

        if DEBUG:
            print("-- Sending data --", file=sys.stderr)
            print_hex(data)
            print("-- ------------- --", file=sys.stderr)
            print("Sent %d bytes" % len(data), file=sys.stderr)

        fail_count = 0
        while True:
            if not self._socket.send(data):
                raise Exception("Error Sending message.")
            resp = receive_data(self._socket)

            if response_busy(resp):
                fail_count += 1
                if fail_count < MAX_ATTEMPTS:
                    time.sleep(DELAY * fail_count)
                else:
                    break
            else:
                break

        return verify_and_strip(resp)

    def get_version(self):
        """ Return version string of SATEL Integra"""

        resp = self.send_command( b'\x7E')
        model = self._hardware_model(resp[0])
        version = format("%c.%c%c %c%c%c%c-%c%c-%c%c" % tuple([chr(x) for x in resp[1:12]]))
        if resp[12] == 1:
            language = "English"
        else:
            language = "Other"

        if resp[13] == 255:
            settings = "stored"
        else:
            settings = "NOT STORED"
        return model + " " + version + " LANG: " + language + " SETTINGS " + settings + " in flash"

    def get_name(self, devicenumber, devicetype):
        r = self.send_command( b'\xEE' + devicetype + devicenumber.to_bytes(1, 'big'))
        return r[3:].decode(encoding).strip()

    def get_status(self):
        return self._status

    def update_arming_status(self):
        """ Check which partitions are armed"""
        r = self.send_command( b'\x0A')

        armed_partitions_indexes = list_set_bits(r, 4)

        if len(armed_partitions_indexes) > 0:
            self._status = AlarmState.ARMED_MODE0
        else:
            self._status = AlarmState.DISARMED

#        self.debug_print_armed_partitions(armed_partitions_indexes)

        return self._status

    def debug_print_armed_partitions(self,partitions):
        print("Armed partitions:")
        for partition_id in [partitions]:
            print(self.get_device_name(DeviceType.PARTITION.value, partition_id))

    def arm(self, code):
        while len(code) < 16:
            code += 'F'

        code_bytes = bytearray.fromhex(code)

        r = self.send_command( b'\x80' + code_bytes + b'\x01\x00\x00\x00')
        res = ArmingResult(r[0])
        if res == ArmingResult.OK:
            self._status = AlarmState.ARMED_MODE0
        return res

    def get_active_outputs(self):
        r = self.send_command( b'\x17')
        return list_set_bits(r, 16)

    def get_new_data_in_commands(self):
        """  List of new data in returned cmds. Used to monitor "what's changed since last check?" """
        r = self.send_command( b'\x7F')

        return list_set_bits(r, 5)

    def update_full_state(self):
        for command_code, name, bytes in self._update_commands:
            print("Getting status for: ", name)
            r = self.send_command( command_code)
            self._current_status[name] = list_set_bits(r, bytes)

        self.print_violated_zones()

    def print_violated_zones(self):
        print("Violated zones:")
        for violated_zones_code in self._current_status["zones violation"]:
            print(self.get_device_name(b'\x01', violated_zones_code))

    def get_time(self):
        """Return string with clock time of the system."""
        r = self.send_command( b'\x1A')
        itime = r[0:2].hex() + "-" + r[2:3].hex() + "-" + r[3:4].hex() + " " + \
                r[4:5].hex() + ":" + r[5:6].hex() + ":" + r[6:7].hex()
        return itime

    def update_device_name(self, device_type, device_number):
        byte_device_number = device_number.to_bytes(1, 'big')
        r = self.send_command( b'\xEE' + device_type + byte_device_number)

        device_name = ""

        # We got error #08, probably undefined device, it's OK, return empty string
        if len(r) == 1 and r[0:1] == b'\x08':
            return device_name

        device_name = r[3:19].decode(encoding)
        self._device_name_cache[device_type, byte_device_number] = device_name
        print("Number: ", device_number, ", name: ", device_name)

        return device_name

    def update_device_names(self):
        ''' Iterate over devices and get their names until fail returned.
            A "device' can be user, partition, zone, imput. The idea here is
            to pre-cache entries up front so not to ask every time.
            This funtion may not find all of the names, only first ones.'''

        for device_type, type_name, max_devices in self._device_types:
            for device_number in range(1, max_devices):
                if not self.update_device_name(device_type, device_number):
                    break
        print(self._device_name_cache)

    def get_device_name(self, device_type, device_number):
        if (device_type, device_number) in self._device_name_cache:
            return self._device_name_cache[device_type, device_number]
        else:
            return self.update_device_name(device_type, device_number)

    def start_monitoring_zones(self):
        r = self.send_command(b'\x7F\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00')

        if r != b'\xFF':
            raise Exception("Monitoring not accepted.")

        while True:
            print("Receiving...")

            resp = receive_data(self._socket)
            r = verify_and_strip(resp)
            if not resp:
                print("Empty response - reconnecting!")
                self._socket = socket(AF_INET, SOCK_STREAM)
                self._socket.connect((config.host, config.port))

                break

            print("Updating...")
            self._current_status["zones violation"] = list_set_bits(r, 32)

            self.print_violated_zones()


def demo(host,port):
    #stl = SatelEthm(host,port)
    #stl.connect()
    sock = socket(AF_INET, SOCK_STREAM)
    sock.connect((host,port))
    stl = SatelEthm(sock)

    print("Connected, the version is...")
    print(stl.get_version())

    print("Integra time: " + stl.get_time())

    stl.update_arming_status()
    #exit(0)

    print("Updating names...")
    stl.update_device_names()

    print("Updating full state...")
    stl.update_full_state()

    print("Starting monitoring ...")
    stl.start_monitoring_zones()

    print("Thanks for watching.")

import asyncio

class AsyncSatel():
    def __init__(self, host, port, monitored_zones, loop):
        """Init the Satel alarm panel."""
        self._host = host
        self._port = port
        self._loop = loop
        self._current_status = {}
        self._message_handlers = {}
        self._monitored_zones = monitored_zones

        self._update_commands = {
            b'\x00': ("zones violation", 16, self.zone_violation ),
            b'\x0A': ("armed partitions (really)", 4, lambda msg: self.armed(1,msg)),
            b'\x0B': ("partitions armed in mode 2", 4, lambda msg: self.armed(2,msg)),
            b'\x0C': ("partitions armed in mode 3", 4, lambda msg: self.armed(3,msg)),
            b'\x13': ("partitions alarm", 4, lambda msg: self.alarm(3,msg)),
            b'\x14': ("partitions fire alarm", 4, lambda msg: self.alarm(3,True,msg)),
        }
        # Assign handler
        self._message_handlers[b'\x00'] = self._update_commands[b'\x00'][2]


    @asyncio.coroutine
    def connect(self):
        self._reader, self._writer = yield from asyncio.open_connection(self._host, self._port,loop=self._loop)
        yield from self._start_monitoring()

    @asyncio.coroutine
    def _start_monitoring(self):

        data = generate_query(b'\x7F\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00')

        yield from self._send_data(data)
        resp = yield from self._read_data()

        if resp[1:2] != b'\xFF':
            raise Exception("Monitoring not accepted.")

    def zone_violation(self, msg):

        status = {"zones":{}}

        violated_zones = list_set_bits(msg, 32)
        _LOGGER.debug("Violated zones: %s, monitored zones: %s", violated_zones, self._monitored_zones )
        for zone in self._monitored_zones:
            status["zones"][zone] = \
                1 if zone in violated_zones else 0

        _LOGGER.debug("Returning status: %s",status)
        return status

    @asyncio.coroutine
    def _send_data(self, data):
        if DEBUG:
            print("-- Sending data --", file=sys.stderr)
            print_hex(data)
            print("-- ------------- --", file=sys.stderr)
            print("Sent %d bytes" % len(data), file=sys.stderr)

        self._writer.write(data)
        yield from self._writer.drain()

    @asyncio.coroutine
    def arm(self, code):
        yield from asyncio.sleep(1)

        while len(code) < 16:
            code += 'F'

        code_bytes = bytearray.fromhex(code)

        data = generate_query(b'\x80' + code_bytes + b'\x01\x00\x00\x00')

        yield from self._send_data(data)
        #resp = yield from self._read_data()

        #res = ArmingResult(resp[1])
        #if res == ArmingResult.OK:
        #    _LOGGER.debug("Armed OK")
        #    self._status = AlarmState.ARMED_MODE0
        #else:
        #    _LOGGER.debug("Not armed - error: %s",res)

    def armed(self, mode, msg):
        _LOGGER.debug("Alarm update!")
        message = {"alarm_status": "armed"}

    def alarm(self, msg, fire=False ):
        pass

    def _read_data(self):
        #data = yield from self._reader.read(100)
        data = yield from self._reader.readuntil(b'\xFE\x0D')
        _LOGGER.debug("-- Receving data --")
        print_hex(data)
        _LOGGER.debug("-- ------------- --")
        return verify_and_strip(data)

    def get_status(self):
        _LOGGER.debug("Wait...")

        resp = yield from self._read_data()

        id = resp[0:1]
        if id in self._message_handlers:
            _LOGGER.info("Setting result on :%s", id)
            return self._message_handlers[id](resp)
        else:
            _LOGGER.info("Ignoring message: %s", id )
            return None

    def send_command(self, command, expected_response_id = None):
        if not expected_response_id:
            expected_response_id = command[0:1]
        self._response_handle_futures[expected_response_id] = asyncio.Future()

        data = generate_query(command)

        if DEBUG:
            print("-- Sending data --", file=sys.stderr)
            print_hex(data)
            print("-- ------------- --", file=sys.stderr)
            print("Sent %d bytes" % len(data), file=sys.stderr)

        self._writer.write(data)

        return True



def demo2(host, port):
    logging.basicConfig(level=logging.DEBUG)

    # stl = SatelEthm(host,port)
    # stl.connect()
    sock = socket(AF_INET, SOCK_STREAM)

    loop = asyncio.get_event_loop()
    stl = AsyncSatel(host, port, [1,2,3], loop)

    @asyncio.coroutine
    def update_satel_status():
        while (True):
            status = yield from stl.get_status()


    loop.run_until_complete(
        stl.connect())

    loop.run_until_complete(
        stl.arm("3333"))

    loop.run_until_complete(asyncio.ensure_future(update_satel_status()))
    loop.close()

    #tasks = [
    #          loop.create_task(stl.read_data()),
    #          loop.create_task(stl.get_name(ZONE, b'\x01')),
    #          loop.create_task(stl.start_monitoring_zones()),
    #          loop.create_task(stl.get_name(ZONE, b'\x01')),

              #loop.create_task(stl.read_data()),
        #            loop.create_task(stl.get_name(ZONE, b'\x01')),
 #             loop.create_task(stl.get_name(ZONE, b'\x01')),
#              loop.create_task(stl.get_name(ZONE, b'\x01')),
#              ]
