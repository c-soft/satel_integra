import pytest

from satel_integra.commands import SatelReadCommand
from satel_integra.const import FRAME_END, FRAME_START
from satel_integra.messages import (
    SatelReadMessage,
    SatelWriteMessage,
    SatelZoneTemperatureReadMessage,
)
from satel_integra.utils import checksum


def test_decode_frame_returns_zone_temperature_message() -> None:
    payload = bytearray([0x7D, 0x01, 0x00, 0x96])
    csum = checksum(payload)
    frame = (
        bytearray(FRAME_START)
        + payload
        + bytearray([csum >> 8, csum & 0xFF])
        + bytearray(FRAME_END)
    )

    msg = SatelReadMessage.decode_frame(frame)

    assert isinstance(msg, SatelZoneTemperatureReadMessage)
    assert msg.zone_id == 1
    assert msg.temperature == 20.0


def test_zone_temperature_message_validates_payload_length() -> None:
    with pytest.raises(ValueError, match="Invalid temperature response length"):
        SatelZoneTemperatureReadMessage(
            SatelReadCommand.ZONE_TEMPERATURE, bytearray([1, 0x00])
        )


def test_write_message_encodes_read_query_command() -> None:
    msg = SatelWriteMessage(SatelReadCommand.RTC_AND_STATUS)
    frame = msg.encode_frame()

    assert frame.startswith(bytearray(FRAME_START))
    assert frame[2] == SatelReadCommand.RTC_AND_STATUS
    assert frame.endswith(bytearray(FRAME_END))


def test_write_message_rejects_result_command() -> None:
    with pytest.raises(ValueError, match="RESULT cannot be sent"):
        SatelWriteMessage(SatelReadCommand.RESULT)
