import logging
from unittest.mock import MagicMock

import pytest

from satel_integra.commands import SatelReadCommand
from satel_integra.message_handlers import MessageHandlerRegistry, SatelSubscriptions
from satel_integra.messages import (
    READ_COMMAND_SPECS,
    SatelReadMessage,
    SatelTypedReadMessage,
)
from satel_integra.models import SatelPanelInfo, SatelResultCode
from satel_integra.state import AlarmState, SatelOutputState, SatelState, SatelZoneState


def _typed_message(
    command: SatelReadCommand,
    payload: bytearray,
) -> SatelTypedReadMessage:
    message = READ_COMMAND_SPECS[command].construct(command, payload)
    assert isinstance(message, SatelTypedReadMessage)
    return message


def _panel_info(type_code: int) -> SatelPanelInfo:
    message = _typed_message(
        SatelReadCommand.INTEGRA_VERSION,
        bytearray([type_code]) + bytearray(b"12120230221") + bytearray([0x00, 0xFF]),
    )
    assert isinstance(message.data, SatelPanelInfo)
    return message.data


@pytest.fixture
def state() -> SatelState:
    return SatelState()


@pytest.fixture
def subscriptions() -> SatelSubscriptions:
    return SatelSubscriptions()


@pytest.fixture
def registry(
    state: SatelState,
    subscriptions: SatelSubscriptions,
) -> MessageHandlerRegistry:
    return MessageHandlerRegistry(state, subscriptions)


@pytest.mark.parametrize(
    ("command", "alarm_state"),
    [
        (
            SatelReadCommand.PARTITIONS_ARMED_SUPPRESSED,
            AlarmState.ARMED_SUPPRESSED,
        ),
        (SatelReadCommand.PARTITIONS_ARMED_MODE0, AlarmState.ARMED_MODE0),
        (SatelReadCommand.PARTITIONS_ARMED_MODE1, AlarmState.ARMED_MODE1),
        (SatelReadCommand.PARTITIONS_ARMED_MODE2, AlarmState.ARMED_MODE2),
        (SatelReadCommand.PARTITIONS_ARMED_MODE3, AlarmState.ARMED_MODE3),
        (SatelReadCommand.PARTITIONS_ENTRY_TIME, AlarmState.ENTRY_TIME),
        (
            SatelReadCommand.PARTITIONS_EXIT_COUNTDOWN_OVER_10,
            AlarmState.EXIT_COUNTDOWN_OVER_10,
        ),
        (
            SatelReadCommand.PARTITIONS_EXIT_COUNTDOWN_UNDER_10,
            AlarmState.EXIT_COUNTDOWN_UNDER_10,
        ),
        (SatelReadCommand.PARTITIONS_ALARM, AlarmState.TRIGGERED),
        (SatelReadCommand.PARTITIONS_FIRE_ALARM, AlarmState.TRIGGERED_FIRE),
    ],
)
def test_partition_state_updates_and_notifies(
    registry: MessageHandlerRegistry,
    state: SatelState,
    subscriptions: SatelSubscriptions,
    command: SatelReadCommand,
    alarm_state: AlarmState,
) -> None:
    payload = bytearray(4)
    payload[0] = 0b00000001  # partition 1
    payload[3] = 0b10000000  # partition 32
    callback = MagicMock()
    subscriptions.partitions.add(callback)

    registry.dispatch(_typed_message(command, payload))

    assert state.partition_states[alarm_state] == [1, 32]
    callback.assert_called_once_with()


def test_partition_state_keeps_fixed_protocol_width(
    registry: MessageHandlerRegistry,
    state: SatelState,
) -> None:
    state.configure_for_panel(_panel_info(0))
    payload = bytearray(4)
    payload[3] = 0b10000000  # partition 32

    registry.dispatch(_typed_message(SatelReadCommand.PARTITIONS_ARMED_MODE0, payload))

    assert state.partition_states[AlarmState.ARMED_MODE0] == [32]


@pytest.mark.parametrize("length", [16, 32])
def test_zone_violations_update_represented_zone_states(
    registry: MessageHandlerRegistry,
    state: SatelState,
    length: int,
) -> None:
    data = bytearray(length)
    data[0] = 0b00000010  # zone 2

    registry.dispatch(_typed_message(SatelReadCommand.ZONES_VIOLATED, data))

    assert len(state.zones) == 256
    assert state.zones[1].violated is False
    assert state.zones[2].violated is True
    assert state.zones[length * 8].violated is False
    if length == 16:
        assert state.zones[129].violated is None


def test_zone_violations_respect_panel_capacity(
    registry: MessageHandlerRegistry,
    state: SatelState,
) -> None:
    state.configure_for_panel(_panel_info(0))
    data = bytearray(16)
    data[0] = 0b00000010  # zone 2
    data[3] = 0b00000001  # unsupported zone 25

    registry.dispatch(_typed_message(SatelReadCommand.ZONES_VIOLATED, data))

    assert len(state.zones) == 24
    assert state.zones[2].violated is True
    assert 25 not in state.zones


def test_zone_violations_update_existing_zone_state(
    registry: MessageHandlerRegistry,
    state: SatelState,
) -> None:
    zone_state = SatelZoneState()
    state.zones[1] = zone_state
    message = _typed_message(
        SatelReadCommand.ZONES_VIOLATED,
        bytearray([0b00000001]) + bytearray(15),
    )

    registry.dispatch(message)

    assert state.zones[1] is zone_state
    assert zone_state.violated is True


@pytest.mark.parametrize("length", [16, 32])
def test_output_states_update_represented_outputs(
    registry: MessageHandlerRegistry,
    state: SatelState,
    length: int,
) -> None:
    data = bytearray(length)
    data[0] = 0b00001000  # output 4

    registry.dispatch(_typed_message(SatelReadCommand.OUTPUTS_STATE, data))

    assert len(state.outputs) == 256
    assert state.outputs[1].active is False
    assert state.outputs[4].active is True
    assert state.outputs[length * 8].active is False
    if length == 16:
        assert state.outputs[129].active is None


def test_output_states_respect_panel_capacity(
    registry: MessageHandlerRegistry,
    state: SatelState,
) -> None:
    state.configure_for_panel(_panel_info(0))
    data = bytearray(16)
    data[0] = 0b00001000  # output 4
    data[3] = 0b00000001  # unsupported output 25

    registry.dispatch(_typed_message(SatelReadCommand.OUTPUTS_STATE, data))

    assert len(state.outputs) == 24
    assert state.outputs[4].active is True
    assert 25 not in state.outputs


def test_output_states_update_existing_output_state(
    registry: MessageHandlerRegistry,
    state: SatelState,
) -> None:
    output_state = SatelOutputState()
    state.outputs[1] = output_state
    message = _typed_message(
        SatelReadCommand.OUTPUTS_STATE,
        bytearray([0b00000001]) + bytearray(15),
    )

    registry.dispatch(message)

    assert state.outputs[1] is output_state
    assert output_state.active is True


def test_changed_system_status_notifies(
    registry: MessageHandlerRegistry,
    state: SatelState,
    subscriptions: SatelSubscriptions,
) -> None:
    clear = _typed_message(SatelReadCommand.RTC_AND_STATUS, bytearray(9))
    active = _typed_message(
        SatelReadCommand.RTC_AND_STATUS,
        bytearray(7) + bytearray([0xC0, 0]),
    )
    registry.dispatch(clear)
    called = []
    subscriptions.system_status.add(lambda: called.append(state.system_status))

    registry.dispatch(active)
    registry.dispatch(clear)

    assert called == [active.data, clear.data]
    assert state.system_status == clear.data


def test_unchanged_system_status_does_not_notify(
    registry: MessageHandlerRegistry,
    subscriptions: SatelSubscriptions,
) -> None:
    message = _typed_message(SatelReadCommand.RTC_AND_STATUS, bytearray(9))
    registry.dispatch(message)
    callback = MagicMock()
    subscriptions.system_status.add(callback)

    registry.dispatch(message)

    callback.assert_not_called()


def test_system_trouble_reports_notify(
    registry: MessageHandlerRegistry,
    state: SatelState,
    subscriptions: SatelSubscriptions,
) -> None:
    clear = _typed_message(SatelReadCommand.TROUBLES_PART_1, bytearray(47))
    payload = bytearray(47)
    payload[40] = 0x80
    active = _typed_message(SatelReadCommand.TROUBLES_PART_1, payload)
    registry.dispatch(clear)
    called = []
    subscriptions.system_status.add(lambda: called.append(state.system_troubles))

    registry.dispatch(active)
    registry.dispatch(clear)

    assert called == [active.data, clear.data]
    assert state.system_troubles == clear.data


def test_repeated_system_trouble_reports_notify(
    registry: MessageHandlerRegistry,
    subscriptions: SatelSubscriptions,
) -> None:
    message = _typed_message(SatelReadCommand.TROUBLES_PART_1, bytearray(47))
    callback = MagicMock()
    subscriptions.system_status.add(callback)

    registry.dispatch(message)
    registry.dispatch(message)

    assert callback.call_count == 2


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (SatelResultCode.COMMAND_ACCEPTED, "COMMAND_ACCEPTED [0xFF]"),
        (SatelResultCode.USER_CODE_NOT_FOUND, "USER_CODE_NOT_FOUND [0x01]"),
    ],
)
def test_command_result_is_logged(
    registry: MessageHandlerRegistry,
    caplog: pytest.LogCaptureFixture,
    code: SatelResultCode,
    expected: str,
) -> None:
    message = _typed_message(SatelReadCommand.RESULT, bytearray([code]))

    with caplog.at_level(logging.DEBUG):
        registry.dispatch(message)

    assert f"Received command result: {expected}" in caplog.text


def test_untyped_command_result_is_rejected(
    registry: MessageHandlerRegistry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    message = SatelReadMessage(SatelReadCommand.RESULT, bytearray([0x01]))

    with caplog.at_level(logging.WARNING):
        registry.dispatch(message)

    assert "Received unexpected message type for RESULT" in caplog.text
