import logging

import pytest

from satel_integra.commands import SatelReadCommand, SatelWriteCommand
from satel_integra.const import FRAME_END, FRAME_START
from satel_integra.exceptions import SatelUnexpectedResponseError
from satel_integra.messages import (
    SatelIntegraVersionReadMessage,
    SatelModuleVersionReadMessage,
    SatelReadMessage,
    SatelWriteMessage,
    SatelZoneInfoReadMessage,
    SatelZoneTemperatureReadMessage,
)
from satel_integra.utils import checksum


def _frame_payload(payload: bytearray) -> bytearray:
    csum = checksum(payload)
    return (
        bytearray(FRAME_START)
        + payload
        + bytearray([csum >> 8, csum & 0xFF])
        + bytearray(FRAME_END)
    )


def test_decode_frame_returns_zone_temperature_message() -> None:
    payload = bytearray([SatelReadCommand.ZONE_TEMPERATURE, 0x01, 0x00, 0x96])

    msg = SatelReadMessage.decode_frame(_frame_payload(payload))

    assert isinstance(msg, SatelZoneTemperatureReadMessage)
    assert msg.zone_id == 1
    assert msg.temperature == 20.0


def test_decode_frame_returns_integra_version_message() -> None:
    payload = (
        bytearray([0x7E, 72]) + bytearray(b"12320120527") + bytearray([0x00, 0xFF])
    )

    msg = SatelReadMessage.decode_frame(_frame_payload(payload))

    assert isinstance(msg, SatelIntegraVersionReadMessage)
    assert msg.panel_info.type_code == 72
    assert msg.panel_info.model is not None
    assert msg.panel_info.model.name == "INTEGRA 256 Plus"
    assert msg.panel_info.firmware.version == "1.23"
    assert msg.panel_info.firmware.release_date.isoformat() == "2012-05-27"
    assert msg.panel_info.language_code == 0
    assert msg.panel_info.settings_stored_in_flash is True


def test_decode_frame_returns_zone_info_message() -> None:
    payload = (
        bytearray([0xEE, 0x05, 0x01, 0x2A])
        + bytearray(b"Front Door      ")
        + bytearray([0x03])
    )

    msg = SatelReadMessage.decode_frame(_frame_payload(payload))

    assert isinstance(msg, SatelZoneInfoReadMessage)
    assert msg.msg_data == payload[1:]


def test_decode_frame_returns_default_message_for_unknown_device_type(caplog) -> None:
    payload = bytearray([0xEE, 0x04, 0x01, 0x10]) + bytearray(b"Output 1        ")

    with caplog.at_level(logging.DEBUG):
        msg = SatelReadMessage.decode_frame(_frame_payload(payload))

    assert type(msg) is SatelReadMessage
    assert msg.cmd is SatelReadCommand.READ_DEVICE_NAME
    assert msg.msg_data == payload[1:]
    assert "Unsupported READ_DEVICE_NAME device type: 0x04" in caplog.text


def test_decode_frame_rejects_missing_device_type() -> None:
    payload = bytearray([0xEE])

    with pytest.raises(
        SatelUnexpectedResponseError,
        match="READ_DEVICE_NAME response missing device type",
    ):
        SatelReadMessage.decode_frame(_frame_payload(payload))


def test_decode_frame_returns_module_version_message() -> None:
    payload = bytearray([0x7C]) + bytearray(b"12320120527") + bytearray([0b0000_0111])

    msg = SatelReadMessage.decode_frame(_frame_payload(payload))

    assert isinstance(msg, SatelModuleVersionReadMessage)
    assert msg.module_info.firmware.version == "1.23"
    assert msg.module_info.firmware.release_date.isoformat() == "2012-05-27"
    assert msg.module_info.supports_256_zones_outputs is True
    assert msg.module_info.supports_trouble_memory_part_8 is True
    assert msg.module_info.supports_arm_no_bypass is True


def test_zone_temperature_message_validates_payload_length(caplog) -> None:
    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(
            SatelUnexpectedResponseError,
            match="Invalid response length for ZONE_TEMPERATURE",
        ),
    ):
        SatelZoneTemperatureReadMessage(
            SatelReadCommand.ZONE_TEMPERATURE, bytearray([1, 0x00])
        )

    assert "payload=0100" in caplog.text


def test_integra_version_message_validates_payload_length(caplog) -> None:
    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(
            SatelUnexpectedResponseError,
            match="Invalid response length for INTEGRA_VERSION",
        ),
    ):
        SatelIntegraVersionReadMessage(SatelReadCommand.INTEGRA_VERSION, bytearray())

    assert "payload=" in caplog.text


def test_module_version_message_validates_payload_length(caplog) -> None:
    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(
            SatelUnexpectedResponseError,
            match="Invalid response length for MODULE_VERSION",
        ),
    ):
        SatelModuleVersionReadMessage(SatelReadCommand.MODULE_VERSION, bytearray())

    assert "payload=" in caplog.text


def test_zone_info_message_validates_payload_length(caplog) -> None:
    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(
            SatelUnexpectedResponseError,
            match="Invalid response length for READ_DEVICE_NAME",
        ),
    ):
        SatelReadMessage.decode_frame(
            _frame_payload(bytearray([0xEE, 0x05, 0x01, 0x2A]))
        )

    assert "payload=05012a" in caplog.text


def test_integra_version_message_rejects_invalid_firmware_payload(caplog) -> None:
    msg = SatelIntegraVersionReadMessage(
        SatelReadCommand.INTEGRA_VERSION,
        bytearray([72]) + bytearray(b"12x20120527") + bytearray([0x00, 0xFF]),
    )

    with (
        caplog.at_level(logging.WARNING),
        pytest.raises(
            SatelUnexpectedResponseError, match="Invalid firmware version payload"
        ),
    ):
        _ = msg.panel_info

    assert "Invalid firmware version payload: '12x20120527'" in caplog.text


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
