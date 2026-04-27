import pytest

from satel_integra.commands import SatelReadCommand, SatelWriteCommand
from satel_integra.const import FRAME_END, FRAME_START
from satel_integra.messages import (
    SatelIntegraVersionReadMessage,
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


def test_decode_frame_returns_integra_version_message() -> None:
    payload = (
        bytearray([0x7E, 72]) + bytearray(b"12320120527") + bytearray([0x00, 0xFF])
    )
    csum = checksum(payload)
    frame = (
        bytearray(FRAME_START)
        + payload
        + bytearray([csum >> 8, csum & 0xFF])
        + bytearray(FRAME_END)
    )

    msg = SatelReadMessage.decode_frame(frame)

    assert isinstance(msg, SatelIntegraVersionReadMessage)
    assert msg.panel_info.type_code == 72
    assert msg.panel_info.model is not None
    assert msg.panel_info.model.name == "INTEGRA 256 Plus"
    assert msg.panel_info.firmware.version == "1.23"
    assert msg.panel_info.firmware.release_date.isoformat() == "2012-05-27"
    assert msg.panel_info.language_code == 0
    assert msg.panel_info.settings_stored_in_flash is True


def test_zone_temperature_message_validates_payload_length() -> None:
    with pytest.raises(ValueError, match="Invalid temperature response length"):
        SatelZoneTemperatureReadMessage(
            SatelReadCommand.ZONE_TEMPERATURE, bytearray([1, 0x00])
        )


def test_integra_version_message_validates_payload_length() -> None:
    with pytest.raises(ValueError, match="Invalid INTEGRA version response length"):
        SatelIntegraVersionReadMessage(SatelReadCommand.INTEGRA_VERSION, bytearray())


def test_write_message_encodes_read_query_command() -> None:
    msg = SatelWriteMessage(SatelReadCommand.RTC_AND_STATUS)
    frame = msg.encode_frame()

    assert frame.startswith(bytearray(FRAME_START))
    assert frame[2] == SatelReadCommand.RTC_AND_STATUS
    assert frame.endswith(bytearray(FRAME_END))


def test_write_message_rejects_result_command() -> None:
    with pytest.raises(ValueError, match="RESULT cannot be sent"):
        SatelWriteMessage(SatelReadCommand.RESULT)


def test_write_message_warns_for_deprecated_write_query_command() -> None:
    with pytest.warns(DeprecationWarning, match="SatelReadCommand.ZONE_TEMPERATURE"):
        SatelWriteMessage(
            SatelWriteCommand.ZONE_TEMPERATURE, raw_data=bytearray([0x01])
        )
