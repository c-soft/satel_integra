# -*- coding: utf-8 -*-

"""Main module."""

from socket import *
import time
import sys


from enum import Enum, unique

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


def send_command(sock, command):
    data = generate_query(command)

    if DEBUG:
        print("-- Sending data --", file=sys.stderr)
        print_hex(data)
        print("-- ------------- --", file=sys.stderr)
        print("Sent %d bytes" % len(data), file=sys.stderr)

    failcount = 0
    while True:
        if not sock.send(data):
            raise Exception("Error Sending message.")
        resp = receive_data(sock)
        # integra will respond "Busy!" if it gets next message too early
        if (resp[0:8] == b'\x10\x42\x75\x73\x79\x21\x0D\x0A'):
            failcount = failcount + 1
            if failcount < MAX_ATTEMPTS:
                time.sleep(DELAY * failcount)
            else:
                break
        else:
            break

    return verify_and_strip(resp)


def receive_data(sock):
    resp = sock.recv(100)
    if DEBUG:
        print("-- Receving data --", file=sys.stderr)
        print_hex(resp)
        print("-- ------------- --", file=sys.stderr)
    return resp


def print_hex(data):
    hex_msg = ""
    for c in data: hex_msg += "\\x" + format(c, "02x")
    print(hex_msg, file=sys.stderr)


def verify_and_strip(resp):
    if (resp[0:2] != b'\xFE\xFE'):
        for c in resp:
            print("0x%X" % c)
        raise Exception("Wrong header - got %X%X" % (resp[0], resp[1]))
    if (resp[-2:] != b'\xFE\x0D'):
        raise Exception("Wrong footer - got %X%X" % (resp[-2], resp[-1]))
    output = resp[2:-2].replace(b'\xFE\xF0', b'\xFE')

    c = checksum(bytearray(output[0:-2]))

    if (256 * output[-2:-1][0] + output[-1:][0]) != c:
        raise Exception("Wrong checksum - got %d expected %d" % ((256 * output[-2:-1][0] + output[-1:][0]), c))

    return output[1:-2]


def list_set_bits(r, expected_length):
    set_bit_numbers = []
    bit_index = 0x1
    if (len(r) != expected_length):
        print("Expected: ", expected_length, "got ", len(r))
    assert (len(r) == expected_length)

    for b in r:
        for i in range(8):
            if ((b >> i) & 1) == 1:
                set_bit_numbers.append(bit_index)
            bit_index += 1
    print("Bits set:")
    msg = ""
    for p in set_bit_numbers: msg += format(p, "#04X") + " "
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


''' Switches the state of a given output
'''


def iSwitchOutput(code, output):
    while len(code) < 16:
        code += b'\x0F'
    output = outputAsString(output)
    cmd = "91" + code + output
    r = send_command(sock, cmd)


def generate_query(command):
    data = bytearray(command)
    c = checksum(data)
    data.append(c >> 8)
    data.append(c & 0xFF)
    data.replace(b'\xFE', b'\xFE\xF0')

    data = bytearray.fromhex("FEFE") + data + bytearray.fromhex("FE0D")
    return data


def iArmInMode0(sock, code):
    while len(code) < 16:
        code += 'F'

    code_bytes = bytearray.fromhex(code)

    r = send_command(sock, b'\x80' + code_bytes + b'\x01\x00\x00\x00')


#### BASIC DEMO
''' ... and now it is the time to demonstrate what have we learnt today
'''
if len(sys.argv) < 1:
    print("Execution: %s IP_ADDRESS_OF_THE_ETHM1_MODULE" % sys.argv[0], file=sys.stderr)
    sys.exit(1)


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


class Partition():
    def __init__(self, name="Partition", status=AlarmState.DISARMED):
        """Init the Satel alarm panel."""
        self._name = name
        self._status = status


class SatelEthm():
    """Class wrapping the integration protocol to the Satel ETHM network module."""

    def __init__(self, socket):
        """Init the Satel alarm panel."""
        self._socket = socket
        self._status = AlarmState.DISARMED
        self._partitions = {}
        self._host = socket.host
        self._port = socket.port

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

    def get_version(self):
        """ Return version string of SATEL Integra"""

        resp = send_command(self._socket, b'\x7E')
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
        r = send_command(self._socket, b'\xEE' + devicetype + devicenumber.to_bytes(1, 'big'))
        return r[3:].decode(encoding).strip()

    def get_status(self):
        return self._status

    def update_arming_status(self):
        """ Check which partitions are armed"""
        r = send_command(self._socket, b'\x0A')
        armed_partitions_indexes = list_set_bits(r, 4)

        if len(armed_partitions_indexes) > 0:
            self._status = AlarmState.ARMED_MODE0
        else:
            self._status = AlarmState.DISARMED

        return self._status

    def debug_print_armed_partitions(self):
        print("Armed partitions:")
        for partition_id in [1]:
            print(self.get_device_name(DeviceType.PARTITION.value, partition_id))

    def arm(self, code):
        while len(code) < 16:
            code += 'F'

        code_bytes = bytearray.fromhex(code)

        r = send_command(self._socket, b'\x80' + code_bytes + b'\x01\x00\x00\x00')
        res = ArmingResult(r[0])
        if res == ArmingResult.OK:
            self._status = AlarmState.ARMED_MODE0
        return res

    def get_active_outputs(self):
        r = send_command(self._socket, b'\x17')
        return list_set_bits(r, 16)

    def get_new_data_in_commands(self):
        """  List of new data in returned cmds. Used to monitor "what's changed since last check?" """
        r = send_command(self._socket, b'\x7F')

        return list_set_bits(r, 5)

    def update_full_state(self):
        for command_code, name, bytes in self._update_commands:
            print("Getting status for: ", name)
            r = send_command(self._socket, command_code)
            self._current_status[name] = list_set_bits(r, bytes)

        self.print_violated_zones()

    def print_violated_zones(self):
        print("Violated zones:")
        for violated_zones_code in self._current_status["zones violation"]:
            print(self.get_device_name(b'\x01', violated_zones_code))

    def get_time(self):
        """Return string with clock time of the system."""
        r = send_command(self._socket, b'\x1A')
        itime = r[0:2].hex() + "-" + r[2:3].hex() + "-" + r[3:4].hex() + " " + \
                r[4:5].hex() + ":" + r[5:6].hex() + ":" + r[6:7].hex()
        return itime

    def update_device_name(self, device_type, device_number):
        byte_device_number = device_number.to_bytes(1, 'big')
        r = send_command(self._socket, b'\xEE' + device_type + byte_device_number)

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
        while True:
            r = send_command(self._socket, b'\x7F\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00')

            if r != b'\xFF': raise Exception("Monitoring not accepted.")

            while True:

                print("Receiving...")

                resp = receive_data(self._socket)
                r = verify_and_strip(resp)
                if not resp:
                    print("Empty response - reconnecting!")
                    self._socket = socket(AF_INET, SOCK_STREAM)
                    self._socket.connect((self._host, self._port))

                    break

                print("Updating...")
                self._current_status["zones violation"] = list_set_bits(r, 32)

                self.print_violated_zones()


def demo(host,port):
    sock_main = socket(AF_INET, SOCK_STREAM)
    sock_main.connect((host,port))

    stl = SatelEthm(sock_main)

    print("Connected, the version is...")
    print(stl.get_version())

    print("Integra time: " + stl.get_time())

    stl.update_arming_status()
    exit(0)

    print("Updating names...")
    stl.update_device_names()

    print("Updating full state...")
    stl.update_full_state()

    print("Starting monitoring ...")
    stl.start_monitoring_zones()
    print("Thanks for watching.")
