import logging

import pytest

from satel_integra.commands import SatelReadCommand, SatelWriteCommand
from satel_integra.const import FRAME_END, FRAME_START
from satel_integra.exceptions import SatelUnexpectedResponseError
from satel_integra.messages import (
    READ_COMMAND_SPECS,
    READ_DEVICE_NAME_SPECS,
    ReadCommandSpec,
    SatelIntegraVersionReadMessage,
    SatelModuleVersionReadMessage,
    SatelOutputInfoReadMessage,
    SatelPartitionInfoReadMessage,
    SatelReadMessage,
    SatelWriteMessage,
    SatelZoneInfoReadMessage,
    SatelZoneTemperatureReadMessage,
)
from satel_integra.models import (
    SatelCommunicationModuleInfo,
    SatelFirmwareVersion,
    SatelOutputInfo,
    SatelPanelInfo,
    SatelPanelModel,
    SatelPartitionInfo,
    SatelZoneInfo,
    SatelZoneTemperature,
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


def _invalid_payload_for_lengths(
    expected_lengths: tuple[int, ...], prefix: bytearray | None = None
) -> bytearray:
    payload = bytearray(prefix or b"")
    while len(payload) in expected_lengths:
        payload.append(0)
    return payload


@pytest.mark.parametrize(
    "payload,message_type,expected_data",
    [
        (
            bytearray([SatelReadCommand.ZONE_TEMPERATURE, 0x01, 0x00, 0x96]),
            SatelZoneTemperatureReadMessage,
            SatelZoneTemperature(zone_id=1, temperature=20.0),
        ),
        (
            bytearray([SatelReadCommand.INTEGRA_VERSION, 72])
            + bytearray(b"12320120527")
            + bytearray([0x00, 0xFF]),
            SatelIntegraVersionReadMessage,
            SatelPanelInfo(
                type_code=72,
                model=SatelPanelModel("INTEGRA 256 Plus"),
                firmware=SatelFirmwareVersion("1.23", "2012-05-27"),
                language_code=0,
                settings_stored_in_flash=True,
            ),
        ),
        (
            bytearray([SatelReadCommand.READ_DEVICE_NAME, 0x05, 0x01, 0x2A])
            + bytearray(b"Front Door      ")
            + bytearray([0x03]),
            SatelZoneInfoReadMessage,
            SatelZoneInfo(
                device_number=1,
                name="Front Door",
                type_code=0x2A,
                partition_assignment=3,
            ),
        ),
        (
            bytearray([SatelReadCommand.READ_DEVICE_NAME, 0x10, 0x01, 0x03])
            + bytearray(b"Ground Floor    ")
            + bytearray([0x02]),
            SatelPartitionInfoReadMessage,
            SatelPartitionInfo(
                device_number=1,
                name="Ground Floor",
                type_code=0x03,
                object_assignment=2,
            ),
        ),
        (
            bytearray([SatelReadCommand.READ_DEVICE_NAME, 0x04, 0x01, 0x10])
            + bytearray(b"Output 1        "),
            SatelOutputInfoReadMessage,
            SatelOutputInfo(
                device_number=1,
                name="Output 1",
                type_code=0x10,
            ),
        ),
        (
            bytearray([SatelReadCommand.MODULE_VERSION])
            + bytearray(b"12320120527")
            + bytearray([0b0000_0111]),
            SatelModuleVersionReadMessage,
            SatelCommunicationModuleInfo(
                firmware=SatelFirmwareVersion("1.23", "2012-05-27"),
                supports_256_zones_outputs=True,
                supports_trouble_memory_part_8=True,
                supports_arm_no_bypass=True,
            ),
        ),
    ],
)
def test_decode_frame_returns_typed_read_message(
    payload, message_type, expected_data
) -> None:
    msg = SatelReadMessage.decode_frame(_frame_payload(payload))

    assert isinstance(msg, message_type)
    assert msg.msg_data == payload[1:]
    assert msg.data == expected_data


def test_read_message_parsed_property_is_cached(monkeypatch) -> None:
    calls = 0
    original_from_payload = SatelZoneInfo._from_payload

    def from_payload(payload: bytes) -> SatelZoneInfo:
        nonlocal calls
        calls += 1
        return original_from_payload(payload)

    payload = (
        bytearray([0x05, 0x01, 0x2A])
        + bytearray(b"Front Door      ")
        + bytearray([0x03])
    )
    msg = SatelZoneInfoReadMessage(SatelReadCommand.READ_DEVICE_NAME, payload)
    monkeypatch.setattr(SatelZoneInfo, "_from_payload", from_payload)

    first = msg.data
    second = msg.data

    assert first is second
    assert calls == 1


def test_decode_frame_returns_default_message_for_unknown_device_type(caplog) -> None:
    payload = bytearray([0xEE, 0x06, 0x01, 0x10]) + bytearray(b"Device 1        ")

    with caplog.at_level(logging.DEBUG):
        msg = SatelReadMessage.decode_frame(_frame_payload(payload))

    assert type(msg) is SatelReadMessage
    assert msg.cmd is SatelReadCommand.READ_DEVICE_NAME
    assert msg.msg_data == payload[1:]
    assert "Unsupported READ_DEVICE_NAME device type: 0x06" in caplog.text


def test_decode_frame_returns_none_for_unknown_command_byte(caplog) -> None:
    payload = bytearray([0xAB, 0x01, 0x02, 0x03])

    with caplog.at_level(logging.WARNING):
        msg = SatelReadMessage.decode_frame(_frame_payload(payload))

    assert msg is None
    assert "Ignoring unknown command byte: 0xab" in caplog.text


def test_decode_frame_logs_event_messages_at_debug(caplog) -> None:
    payload = bytearray.fromhex("8cbfc554c287fb0301035896035096")

    with caplog.at_level(logging.DEBUG):
        msg = SatelReadMessage.decode_frame(_frame_payload(payload))

    assert type(msg) is SatelReadMessage
    assert msg.cmd is SatelReadCommand.READ_EVENT
    assert msg.msg_data == payload[1:]
    assert "Received event message; event decoding is not implemented" in caplog.text
    assert "Ignoring unknown command byte" not in caplog.text


def test_decode_frame_rejects_missing_device_type() -> None:
    payload = bytearray([0xEE])

    with pytest.raises(
        SatelUnexpectedResponseError,
        match="READ_DEVICE_NAME response missing device type",
    ):
        SatelReadMessage.decode_frame(_frame_payload(payload))


@pytest.mark.parametrize(
    "spec",
    READ_COMMAND_SPECS.values(),
    ids=lambda spec: spec.command.name,
)
def test_decode_frame_uses_read_command_specs(spec) -> None:
    payloads = {
        SatelReadCommand.MODULE_VERSION: bytearray(b"12320120527")
        + bytearray([0b0000_0111]),
        SatelReadCommand.ZONE_TEMPERATURE: bytearray([0x01, 0x00, 0x96]),
        SatelReadCommand.INTEGRA_VERSION: bytearray([72])
        + bytearray(b"12320120527")
        + bytearray([0x00, 0xFF]),
        SatelReadCommand.READ_DEVICE_NAME: bytearray([0x05, 0x01, 0x2A])
        + bytearray(b"Front Door      ")
        + bytearray([0x03]),
    }

    msg = SatelReadMessage.decode_frame(
        _frame_payload(bytearray([spec.command]) + payloads[spec.command])
    )

    assert isinstance(msg, spec.message_type)


def test_decode_frame_uses_spec_expected_data_lengths(monkeypatch) -> None:
    monkeypatch.setitem(
        READ_COMMAND_SPECS,
        SatelReadCommand.RTC_AND_STATUS,
        ReadCommandSpec(
            command=SatelReadCommand.RTC_AND_STATUS,
            message_type=SatelReadMessage,
            expected_data_lengths=(2,),
        ),
    )

    msg = SatelReadMessage.decode_frame(
        _frame_payload(bytearray([SatelReadCommand.RTC_AND_STATUS, 0x01, 0x02]))
    )

    assert type(msg) is SatelReadMessage

    with pytest.raises(
        SatelUnexpectedResponseError,
        match="Invalid response length for RTC_AND_STATUS",
    ):
        SatelReadMessage.decode_frame(
            _frame_payload(bytearray([SatelReadCommand.RTC_AND_STATUS, 0x01]))
        )


@pytest.mark.parametrize(
    "spec",
    [
        spec
        for spec in READ_COMMAND_SPECS.values()
        if spec.expected_data_lengths is not None and spec.decoder is None
    ],
    ids=lambda spec: spec.command.name,
)
def test_decode_frame_validates_command_spec_payload_lengths(spec) -> None:
    assert spec.expected_data_lengths is not None

    with (
        pytest.raises(
            SatelUnexpectedResponseError,
            match=f"Invalid response length for {spec.command.name}",
        ),
    ):
        SatelReadMessage.decode_frame(
            _frame_payload(
                bytearray([spec.command])
                + _invalid_payload_for_lengths(spec.expected_data_lengths)
            )
        )


@pytest.mark.parametrize(
    "selector",
    READ_DEVICE_NAME_SPECS,
    ids=lambda selector: selector.name,
)
def test_decode_frame_validates_device_name_spec_payload_lengths(selector) -> None:
    spec = READ_DEVICE_NAME_SPECS[selector]
    assert spec.expected_data_lengths is not None

    payload = _invalid_payload_for_lengths(
        spec.expected_data_lengths, bytearray([selector])
    )

    with (
        pytest.raises(
            SatelUnexpectedResponseError,
            match="Invalid response length for READ_DEVICE_NAME",
        ),
    ):
        SatelReadMessage.decode_frame(
            _frame_payload(bytearray([SatelReadCommand.READ_DEVICE_NAME]) + payload)
        )


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
        _ = msg.data

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
