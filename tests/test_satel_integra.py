#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Tests for `satel_integra` package."""

import pytest

# from click.testing import CliRunner

# from satel_integra import cli
from unittest import TestCase
from satel_integra.satel_integra import \
    checksum, generate_query, verify_and_strip

# import unittest
# from unittest.mock import MagicMock


@pytest.fixture
def response():
    """Sample pytest fixture.

    See more at: http://doc.pytest.org/en/latest/fixture.html
    """
    # import requests
    # return requests.get('https://github.com/audreyr/cookiecutter-pypackage')


def test_command_line_interface():
    """Test the CLI."""
    # runner = CliRunner()
    # result = runner.invoke(cli.main)
    # assert result.exit_code == 0
    # assert 'satel_integra.cli.main' in result.output
    # help_result = runner.invoke(cli.main, ['--help'])
    # assert help_result.exit_code == 0
    #    assert '--help  Show this message and exit.' in help_result.output
    pass


test_frames = \
    {"Version query": b'\xFE\xFE\x7E\xD8\x60\xFE\x0D',
     "Version response": b'\xFE\xFE\x7E\x03\x31\x31\x36\x32\x30\x31\x36\x30'
                         b'\x37\x31\x35\x00\x00\x02\x48\xFE\x0D',
     "Time query": b'\xFE\xFE\x1a\xd7\xfc\xFE\x0D',
     "Time response": b'\xFE\xFE\x1A\x20\x17\x08\x07\x23\x59\x22\x00\xA3\x34'
                      b'\x70\xFE\x0D',
     "Name query": b'\xFE\xFE\xee\x00\x01\x63\x0a\xfe\x0d',
     "Name response": b'\xFE\xFE\xEE\x00\x01\x00\x53\x74\x72\x65\x66\x61\x20'
                      b'\x20\x31\x20\x20\x20\x20\x20\x20\x20\x5D\x20\xFE\x0D',
     "Start monitoring arm state":
         b'\xfe\xfe\x7f\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xa7'
         b'\xa9\xfe\x0d',
     "Response OK to start monitoring": b'\xfe\xfe\xef\xff\x4f\xa9\xfe\x0d',
     "First partition armed": b'\xfe\xfe\x09\x01\x00\x00\x00\x7d\xac\xfe\x0d',
     "First partition disarmed":
         b'\xfe\xfe\x09\x00\x00\x00\x00\x7d\xb4\xfe\x0d',
     "Output status query": b'\xfe\xfe\x17\xd7\xf9\xfe\x0d',
     "Output status active 16 and 256":
         b'\xfe\xfe\x17\x00\x80\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
         b'\x00\x00\x80\x22\xd8\xfe\x0d',
     "Arm0 query": b'\xfe\xfe\x80\x11\x11\xff\xff\xff\xff\xff\xff\x01\x00'
                   b'\x00\x00\x15\x40\xfe\x0d',
     "Arm0 response": b'\xfe\xfe\xef\x00\x4e\xaa\xfe\x0d',
     "Armed partitions query": b'\xfe\xfe\x0a\xd7\xec\xfe\x0d',
     "Armed partitions response":
         b'\xfe\xfe\x0a\x01\x00\x00\x00\x7d\xbc\xfe\x0d',
     "Active outputs query": b'\xfe\xfe\x17\xd7\xf9\xfe\x0d',
     "Active outputs response":
         b'\xfe\xfe\x17\x00\x80\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
         b'\x00\x00\x80\x22\xd8\xfe\x0d',
     "New data query": b'\xfe\xfe\x7f\xd8\x61\xfe\x0d',
     "New data response": b'\xfe\xfe\x7f\xfe\xf0\xfb\x7f\xfb\xff\xff\xcc\xfe'
                          b'\x0d',
     }


class TestSatel(TestCase):
    """Basic data processing test cases of Satel Integra protocol."""

    def setUp(self):
        """Called before every test."""
        pass

    def tearDown(self):
        """Called after every test."""
        pass

    def test_checksum_generation(self):
        """For each reference data frame verify if checksum is counted OK."""
        for data in test_frames.values():
            # Skipping first 2 sync bytes and 4 ending bytes: 2 bytes of CRC
            #  and 2 bytes trailing
            modified = data[2:-2].replace(b'\xFE\xF0', b'\xFE')
            crc = checksum(modified[:-2])
            expected_crc = modified[-2:]

            res = int.from_bytes(expected_crc, "big")

            self.assertEqual(crc, res)

    def test_version_query_generation(self):
        """Test if version query frame is generated as reference."""
        result = generate_query(b'\x7E')
        self.assertEqual(result, test_frames["Version query"])

    def test_time_query_generation(self):
        """Test if time query frame is generated as reference."""
        result = generate_query(b'\x1a')
        self.assertEqual(result, test_frames["Time query"])

    def test_name_query_generation(self):
        """Test if object name query frame is generated as reference."""
        device_type = b'\x01'
        devicenumber = b'\x00'
        result = generate_query(b'\xEE' + devicenumber + device_type)
        self.assertEqual(result, test_frames["Name query"])

    def test_verify_and_strip(self):
        """Test if verify and strip works ok on reference data."""
        for data in test_frames.values():
            verify_and_strip(data)

# def test_get_version(self):
#     """Connect and retreive Satel Integra Version. Test bases
# on captured frames."""
#     sock = MagicMock()
#     sock.send = MagicMock(return_value=True)
#     sock.recv = MagicMock(return_value=test_frames["Version
# response"])
#
#     satel = SatelEthm(sock)
#     self.assertEqual(satel.get_version(),"INTEGRA 128 1.16
# 2016-07-15 LANG: Other SETTINGS NOT STORED in flash")
#     sock.send.assert_called_with(test_frames["Version query"])
#
# def test_get_name(self):
#     """Connect and retreive Satel Integra Name. Test bases on
# captured frames."""
#     sock = MagicMock()
#     sock.send = MagicMock(return_value=True)
#     sock.recv = MagicMock(return_value=test_frames["Name
# response"])
#
#     satel = SatelEthm(sock)
#     self.assertEqual(satel.get_name(1, PARTITION),"Strefa  1")
#     sock.send.assert_called_with(test_frames["Name query"])
#
# def test_arm_mode0(self):
#     """Arm in mode zero."""
#     sock = MagicMock()
#     sock.send = MagicMock(return_value=True)
#     sock.recv = MagicMock(return_value=test_frames["Arm0
# response"])
#
#     satel = SatelEthm(sock)
#     satel.arm("1111")
#     self.assertEqual(satel.get_status(),AlarmState.ARMED_MODE0)
#     sock.send.assert_called_with(test_frames["Arm0 query"])
#
# def test_update_arming_status(self):
#     """Arm in mode zero."""
#     sock = MagicMock()
#     sock.send = MagicMock(return_value=True)
#     sock.recv = MagicMock(return_value=test_frames["Armed
# partitions response"])
#
#     satel = SatelEthm(sock)
#     satel.update_arming_status()
#     self.assertEqual(satel.get_status(),AlarmState.ARMED_MODE0)
#     sock.send.assert_called_with(test_frames["Armed partitions
#  query"])
#
# def test_get_triggered_outputs(self):
#     """Return the list of outputs that are currently active."""
#     sock = MagicMock()
#     sock.send = MagicMock(return_value=True)
#     sock.recv = MagicMock(return_value=test_frames["Active
# outputs response"])
#
#     satel = SatelEthm(sock)
#     outputs = satel.get_active_outputs()
#     self.assertEqual(outputs,[0X10,0X80])
#     sock.send.assert_called_with(test_frames["Active outputs
# query"])
#
# def test_get_new_data_in_commands(self):
#     """Return the list of outputs that are currently active."""
#     sock = MagicMock()
#     sock.send = MagicMock(return_value=True)
#     sock.recv = MagicMock(return_value=test_frames["New data
# response"])
#
#     satel = SatelEthm(sock)
#     commands = satel.get_new_data_in_commands()
#     self.assertEqual(commands, [0X02,0X03,0X04,0X05,0X06,0X07,
# 0X08,
#                                 0X09,0X0A,0X0C,0X0D,0X0E,0X0F,
# 0X10,0X11,
#                                 0X12,0X13,0X14,0X15,0X16,0x17,
# 0X19,0X1A,
#                                 0X1C,0X1D,0X1E,0X1F,0X20,0X21,
# 0X22,0X23,
#                                 0X24,0X25,0X26,0X27,0x28])
#     sock.send.assert_called_with(test_frames["New data query"])

# def test_update_full_state(self):
#        """Update alarm state should connect and update configuration of
# the alarm."""
#        sock = MagicMock()
#        sock.send = MagicMock(return_value=True)
#        sock.recv = MagicMock(return_value=test_frames["New data response"])

#        satel = SatelEthm(sock)
#        satel.update_full_state()

#        sock.send.assert_called_with(test_frames["New data query"])


# if __name__ == "__main__":
#    unittest.main()
