import pytest

from satel_integra.commands import SatelReadCommand
from satel_integra.const import FRAME_END, FRAME_START
from satel_integra.messages import (
    SatelReadMessage,
    SatelZoneTemperatureReadMessage,
)
from satel_integra.utils import checksum


def test_decode_frame_returns_zone_temperature_message() -> None:
    payload = bytearray([0x7D, 0x00, 0x01, 0x96])
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
