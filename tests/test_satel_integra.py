import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from satel_integra.commands import SatelReadCommand, SatelWriteCommand
from satel_integra.exceptions import (
    SatelCommandRejectedError,
    SatelConnectFailedError,
    SatelConnectionInitializationError,
    SatelConnectionStoppedError,
    SatelMonitoringStartError,
    SatelNoResponseError,
    SatelPanelBusyError,
    SatelUnexpectedResponseError,
)
from satel_integra.messages import (
    READ_COMMAND_SPECS,
    SatelReadMessage,
    SatelTypedReadMessage,
)
from satel_integra.models import (
    SatelCommandResult,
    SatelOutputInfo,
    SatelPartitionInfo,
    SatelResultCode,
    SatelRtcAndStatus,
    SatelZoneInfo,
)
from satel_integra.satel_integra import AlarmState, AsyncSatel
from satel_integra.state import SatelState, SatelZoneState


def _typed_message(
    command: SatelReadCommand,
    payload: bytearray,
) -> SatelTypedReadMessage:
    message = READ_COMMAND_SPECS[command].construct(command, payload)
    assert isinstance(message, SatelTypedReadMessage)
    return message


def _make_result_message(raw_code: int) -> SatelTypedReadMessage:
    return _typed_message(SatelReadCommand.RESULT, bytearray([raw_code]))


class FakeLoop:
    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now


@pytest.fixture
def mock_connection():
    conn = AsyncMock()
    conn.connected = True
    conn.stopped = False
    conn.generation = 1
    conn.last_outbound_activity = None
    conn.ensure_connected = AsyncMock(return_value=True)
    conn.wait_reconnected = AsyncMock(return_value=True)
    conn.wait_stopped = AsyncMock(return_value=None)
    conn.add_connection_state_callback = MagicMock()
    return conn


@pytest.fixture
def mock_queue():
    queue = AsyncMock()
    return queue


@pytest.fixture
def satel(monkeypatch, mock_connection, mock_queue) -> AsyncSatel:
    monkeypatch.setattr(
        "satel_integra.satel_integra.SatelConnection", lambda *a, **kw: mock_connection
    )
    monkeypatch.setattr(
        "satel_integra.satel_integra.SatelMessageQueue",
        lambda send, **kwargs: mock_queue,
    )

    satel = AsyncSatel(
        "127.0.0.1",
        7094,
        monitored_zones=[1, 2],
        monitored_outputs=[3, 4],
        partitions=[1],
    )
    satel._connection = mock_connection
    satel._queue = mock_queue
    mock_queue.on_message_received = MagicMock()
    return satel


@pytest.fixture
def fake_loop(monkeypatch):
    loop = FakeLoop()
    monkeypatch.setattr(
        "satel_integra.satel_integra.asyncio.get_running_loop", lambda: loop
    )
    monkeypatch.setattr("satel_integra.satel_integra.KEEPALIVE_INTERVAL", 5)
    return loop


@pytest.fixture
def fake_sleep_factory(monkeypatch, fake_loop):
    def factory(on_sleep=None):
        sleep_calls: list[float] = []

        async def fake_sleep(duration):
            sleep_calls.append(duration)
            fake_loop.now += duration
            if on_sleep is not None:
                on_sleep(len(sleep_calls), duration, fake_loop)

        monkeypatch.setattr("satel_integra.satel_integra.asyncio.sleep", fake_sleep)
        return sleep_calls

    return factory


@pytest.mark.asyncio
async def test_start_monitoring_success(satel, mock_queue):
    mock_queue.add_message.return_value = _make_result_message(
        SatelResultCode.COMMAND_ACCEPTED
    )

    await satel.start_monitoring()

    mock_queue.add_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_monitoring_sets_monitoring_ready_and_notifies(
    satel, mock_queue, mock_connection
):
    mock_queue.add_message.return_value = _make_result_message(
        SatelResultCode.COMMAND_ACCEPTED
    )
    assert satel.monitoring_ready is False

    await satel.start_monitoring()

    assert satel.monitoring_ready is True
    mock_connection.notify_state_changed.assert_awaited_once()

    # A new connection needs its own acknowledgement.
    mock_connection.generation = 2
    assert satel.monitoring_ready is False


@pytest.mark.asyncio
async def test_monitor_loop_retries_monitoring_on_same_connection(
    satel, mock_connection, fake_sleep_factory
):
    sleep_calls = fake_sleep_factory()
    calls = 0

    async def start_monitoring():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SatelMonitoringStartError("No response to monitoring request")
        satel._monitoring_generation = mock_connection.generation

    satel.start_monitoring = AsyncMock(side_effect=start_monitoring)
    mock_connection.wait_ready_after = AsyncMock(
        side_effect=SatelConnectionStoppedError
    )

    await satel._monitor_reconnection_loop()

    # Previously one unanswered request left monitoring off until the next
    # reconnection; now it is retried on the same connection.
    assert satel.start_monitoring.await_count == 2
    assert sleep_calls == [1.0]
    assert satel.monitoring_ready is True


@pytest.mark.asyncio
async def test_monitor_loop_refreshes_monitoring_on_unchanged_connection(
    satel, mock_connection
):
    satel._monitoring_generation = mock_connection.generation
    satel.start_monitoring = AsyncMock()
    mock_connection.wait_ready_after = AsyncMock(
        side_effect=[TimeoutError, SatelConnectionStoppedError]
    )

    await satel._monitor_reconnection_loop()

    satel.start_monitoring.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_monitoring_rejected(satel, mock_queue):
    mock_queue.add_message.return_value = _make_result_message(
        SatelResultCode.NO_ACCESS
    )

    with pytest.raises(
        SatelMonitoringStartError,
        match="Monitoring startup did not receive a successful acknowledgement",
    ):
        await satel.start_monitoring()


@pytest.mark.asyncio
async def test_start_monitoring_no_response(satel, mock_queue):
    mock_queue.add_message.return_value = None

    with pytest.raises(
        SatelMonitoringStartError, match="No response to monitoring request"
    ):
        await satel.start_monitoring()


@pytest.mark.asyncio
async def test_start_monitoring_unexpected_response(satel, mock_queue):
    mock_queue.add_message.return_value = SatelReadMessage(
        SatelReadCommand.ZONES_VIOLATED, bytearray(32)
    )

    with pytest.raises(
        SatelMonitoringStartError,
        match="Monitoring startup did not receive a successful acknowledgement",
    ):
        await satel.start_monitoring()


def test_zones_violated_callback(satel):
    msg = MagicMock()
    msg.get_active_bits.return_value = [1]

    called = {}
    satel.register_callbacks(zone_changed_callback=lambda status: called.update(status))

    satel._zones_violated(msg)

    assert called == {1: 1, 2: 0}
    assert satel.violated_zones == [1]


def test_zone_states_are_initialized_unknown(satel):
    assert list(satel.state.zones) == list(range(1, 257))
    assert all(state.violated is None for state in satel.state.zones.values())


@pytest.mark.parametrize("length", [16, 32])
def test_zones_violated_accepts_16_and_32_byte_masks(satel, length):
    data = bytearray(length)
    data[0] = 0b00000010  # zone 2
    msg = SatelReadMessage(SatelReadCommand.ZONES_VIOLATED, data)

    called = {}
    satel.register_callbacks(zone_changed_callback=lambda status: called.update(status))

    satel._zones_violated(msg)

    assert called == {1: 0, 2: 1}
    assert satel.violated_zones == [2]


@pytest.mark.parametrize("length", [16, 32])
def test_zones_violated_updates_zone_states(satel, length):
    data = bytearray(length)
    data[0] = 0b00000010  # zone 2
    msg = SatelReadMessage(SatelReadCommand.ZONES_VIOLATED, data)

    satel._zones_violated(msg)

    assert len(satel.state.zones) == 256
    assert satel.state.zones[1].violated is False
    assert satel.state.zones[2].violated is True
    assert satel.state.zones[length * 8].violated is False
    if length == 16:
        assert satel.state.zones[129].violated is None


def test_zones_violated_updates_existing_zone_state(satel):
    zone_state = SatelZoneState()
    satel.state.zones[1] = zone_state
    msg = MagicMock()
    msg.get_active_bits.return_value = [1]

    satel._zones_violated(msg)

    assert satel.state.zones[1] is zone_state
    assert zone_state.violated is True


@pytest.mark.parametrize("length", [16, 32])
def test_outputs_changed_accepts_16_and_32_byte_masks(satel, length):
    data = bytearray(length)
    data[0] = 0b00001000  # output 4
    msg = SatelReadMessage(SatelReadCommand.OUTPUTS_STATE, data)

    satel._outputs_changed(msg)

    assert satel.state.violated_outputs == [4]


def test_outputs_changed_callback(satel):
    msg = MagicMock()
    msg.get_active_bits.return_value = [4]

    called = {}
    satel.register_callbacks(
        output_changed_callback=lambda status: called.update(status)
    )

    satel._outputs_changed(msg)

    assert called == {3: 0, 4: 1}
    assert satel.state.violated_outputs == [4]


def test_partitions_armed_state_callback(satel):
    msg = MagicMock()
    msg.get_active_bits.return_value = [1]
    called = False
    satel.register_callbacks(alarm_status_callback=lambda: nonlocal_set(True))

    # helper to mutate closure var
    def nonlocal_set(val):
        nonlocal called
        called = val

    satel._partitions_armed_state(AlarmState.ARMED_MODE0, msg)

    assert satel.state.partition_states[AlarmState.ARMED_MODE0] == [1]
    assert called


def test_add_zone_changed_callback_notifies_after_state_update(satel):
    msg = MagicMock()
    msg.get_active_bits.return_value = [1]
    observed_zones = []
    callback = MagicMock(
        side_effect=lambda: observed_zones.extend(satel.violated_zones)
    )
    satel.add_zone_changed_callback(callback)

    satel._zones_violated(msg)

    callback.assert_called_once_with()
    assert observed_zones == [1]


def test_add_zone_changed_callback_can_read_zone_states(satel):
    msg = MagicMock()
    msg.get_active_bits.return_value = [1]
    observed_states = []
    callback = MagicMock(
        side_effect=lambda: observed_states.append(
            (
                satel.state.zones[1].violated,
                satel.state.zones[2].violated,
            )
        )
    )
    satel.add_zone_changed_callback(callback)

    satel._zones_violated(msg)

    callback.assert_called_once_with()
    assert observed_states == [(True, False)]


def test_add_output_changed_callback_notifies_after_state_update(satel):
    msg = MagicMock()
    msg.get_active_bits.return_value = [4]
    observed_outputs = []
    callback = MagicMock(
        side_effect=lambda: observed_outputs.extend(satel.state.violated_outputs)
    )
    satel.add_output_changed_callback(callback)

    satel._outputs_changed(msg)

    callback.assert_called_once_with()
    assert observed_outputs == [4]


def test_add_alarm_status_callback(satel):
    msg = MagicMock()
    msg.get_active_bits.return_value = [1]
    callback = MagicMock()
    satel.add_alarm_status_callback(callback)

    satel._partitions_armed_state(AlarmState.ARMED_MODE0, msg)

    callback.assert_called_once_with()


def test_callback_subscriptions_can_be_removed(satel):
    alarm_callback = MagicMock()
    zone_callback = MagicMock()
    output_callback = MagicMock()
    unsubscribe_alarm = satel.add_alarm_status_callback(alarm_callback)
    unsubscribe_zone = satel.add_zone_changed_callback(zone_callback)
    unsubscribe_output = satel.add_output_changed_callback(output_callback)

    unsubscribe_alarm()
    unsubscribe_zone()
    unsubscribe_output()

    msg = MagicMock()
    msg.get_active_bits.return_value = []
    satel._partitions_armed_state(AlarmState.ARMED_MODE0, msg)
    satel._zones_violated(msg)
    satel._outputs_changed(msg)

    alarm_callback.assert_not_called()
    zone_callback.assert_not_called()
    output_callback.assert_not_called()


def test_register_callbacks_is_deprecated(satel):
    alarm_callback = MagicMock()
    zone_callback = MagicMock()

    with pytest.warns(DeprecationWarning, match="register_callbacks is deprecated"):
        satel.register_callbacks(
            alarm_status_callback=alarm_callback,
            zone_changed_callback=zone_callback,
        )

    msg = MagicMock()
    msg.get_active_bits.return_value = []
    satel._partitions_armed_state(AlarmState.ARMED_MODE0, msg)
    satel._zones_violated(msg)

    alarm_callback.assert_called_once_with()
    zone_callback.assert_called_once_with({1: 0, 2: 0})


def test_system_status_callback(satel):
    msg = _typed_message(SatelReadCommand.RTC_AND_STATUS, bytearray(9))
    called = []
    satel.add_system_status_callback(lambda: called.append(satel.state.system_status))

    assert satel.state.system_status is None

    satel._system_status_changed(msg)

    assert called == [msg.data]
    assert satel.state.system_status == msg.data


def test_state_exposes_current_public_state(satel):
    assert isinstance(satel.state, SatelState)
    assert satel.violated_zones == []
    assert satel.state.system_status is None
    assert satel.state.system_troubles is None
    with pytest.raises(AttributeError):
        satel.state = SatelState()

    zones = MagicMock()
    zones.get_active_bits.return_value = [1]
    satel._zones_violated(zones)
    status = _typed_message(SatelReadCommand.RTC_AND_STATUS, bytearray(9))
    troubles = _typed_message(SatelReadCommand.TROUBLES_PART_1, bytearray(47))

    satel._system_status_changed(status)
    satel._system_troubles_changed(troubles)

    assert satel.violated_zones == [1]
    assert satel.state.system_status == status.data
    assert satel.state.system_troubles == troubles.data


def test_system_status_changes_notify(satel):
    clear = _typed_message(SatelReadCommand.RTC_AND_STATUS, bytearray(9))
    active = _typed_message(
        SatelReadCommand.RTC_AND_STATUS, bytearray(7) + bytearray([0xC0, 0])
    )
    satel.state.system_status = clear.data
    called = []
    satel.add_system_status_callback(lambda: called.append(satel.state.system_status))

    satel._system_status_changed(active)
    satel._system_status_changed(clear)

    assert called == [active.data, clear.data]
    assert satel.state.system_status == clear.data


def test_unchanged_system_status_does_not_notify(satel):
    msg = _typed_message(SatelReadCommand.RTC_AND_STATUS, bytearray(9))
    satel.state.system_status = msg.data
    callback = MagicMock()
    satel.add_system_status_callback(callback)

    satel._system_status_changed(msg)

    callback.assert_not_called()


def test_system_status_subscription_can_be_removed(satel):
    callback = MagicMock()
    unsubscribe = satel.add_system_status_callback(callback)
    msg = _typed_message(SatelReadCommand.RTC_AND_STATUS, bytearray(9))
    unsubscribe()

    satel._system_status_changed(msg)

    callback.assert_not_called()


def test_system_troubles_callback(satel):
    msg = _typed_message(SatelReadCommand.TROUBLES_PART_1, bytearray(47))
    called = []
    satel.add_system_status_callback(lambda: called.append(satel.state.system_troubles))

    assert satel.state.system_troubles is None

    satel._system_troubles_changed(msg)

    assert called == [msg.data]
    assert satel.state.system_troubles == msg.data


def test_system_troubles_changes_notify(satel):
    clear = _typed_message(SatelReadCommand.TROUBLES_PART_1, bytearray(47))
    payload = bytearray(47)
    payload[40] = 0x80
    active = _typed_message(SatelReadCommand.TROUBLES_PART_1, payload)
    satel.state.system_troubles = clear.data
    called = []
    satel.add_system_status_callback(lambda: called.append(satel.state.system_troubles))

    satel._system_troubles_changed(active)
    satel._system_troubles_changed(clear)

    assert called == [active.data, clear.data]
    assert satel.state.system_troubles == clear.data


def test_repeated_system_troubles_reports_notify(satel):
    msg = _typed_message(SatelReadCommand.TROUBLES_PART_1, bytearray(47))
    callback = MagicMock()
    satel.add_system_status_callback(callback)

    satel._system_troubles_changed(msg)
    satel._system_troubles_changed(msg)

    assert callback.call_count == 2


def test_command_result_accepted(satel, caplog):
    msg = _make_result_message(SatelResultCode.COMMAND_ACCEPTED)

    with caplog.at_level(logging.DEBUG):
        satel._command_result(msg)

    assert "Received command result: COMMAND_ACCEPTED [0xFF]" in caplog.text


def test_command_result_user_code_not_found(satel, caplog):
    msg = _make_result_message(SatelResultCode.USER_CODE_NOT_FOUND)

    with caplog.at_level(logging.DEBUG):
        satel._command_result(msg)

    assert "Received command result: USER_CODE_NOT_FOUND [0x01]" in caplog.text


def test_command_result_warns_for_untyped_result(satel, caplog):
    msg = SatelReadMessage(SatelReadCommand.RESULT, bytearray([0x01]))

    with caplog.at_level(logging.WARNING):
        satel._command_result(msg)

    assert "Received untyped command result" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,args",
    [
        ("arm", ("1234", [1], 0)),
        ("disarm", ("1234", [1])),
        ("clear_alarm", ("1234", [1])),
        ("set_output", ("1234", 3, True)),
    ],
)
async def test_send_methods_call_queue_add(satel, mock_queue, method, args):
    result = await getattr(satel, method)(*args)

    assert result is None
    mock_queue.add_message.assert_awaited_once()
    assert mock_queue.add_message.await_args.args[1] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,args",
    [
        ("arm", ("9876", [1], 0)),
        ("disarm", ("9876", [1])),
        ("clear_alarm", ("9876", [1])),
        ("set_output", ("9876", 3, True)),
    ],
)
async def test_send_methods_do_not_log_user_code(
    satel, mock_queue, caplog, method, args
):
    with caplog.at_level(logging.DEBUG):
        await getattr(satel, method)(*args)

    assert "9876" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state, command",
    [(True, SatelWriteCommand.OUTPUTS_ON), (False, SatelWriteCommand.OUTPUTS_OFF)],
)
async def test_set_output_waits_for_result(satel, mock_queue, state, command):
    mock_queue.add_message.return_value = _make_result_message(
        SatelResultCode.COMMAND_ACCEPTED
    )

    result = await satel.set_output("1234", 3, state, wait_for_result=True)

    assert isinstance(result, SatelCommandResult)
    assert result.is_success
    sent_msg, wait_for_result = mock_queue.add_message.await_args.args
    assert sent_msg.cmd is command
    assert wait_for_result is True


@pytest.mark.asyncio
async def test_set_output_wait_for_result_rejected(satel, mock_queue):
    mock_queue.add_message.return_value = _make_result_message(
        SatelResultCode.NO_ACCESS
    )

    with pytest.raises(SatelCommandRejectedError) as err:
        await satel.set_output("1234", 3, True, wait_for_result=True)

    assert err.value.result.code == SatelResultCode.NO_ACCESS


@pytest.mark.asyncio
async def test_set_output_wait_for_result_no_response(satel, mock_queue):
    mock_queue.add_message.return_value = None

    with pytest.raises(SatelNoResponseError):
        await satel.set_output("1234", 3, True, wait_for_result=True)


@pytest.mark.asyncio
async def test_set_output_wait_for_result_does_not_log_user_code(
    satel, mock_queue, caplog
):
    mock_queue.add_message.return_value = _make_result_message(
        SatelResultCode.COMMAND_ACCEPTED
    )

    with caplog.at_level(logging.DEBUG):
        await satel.set_output("9876", 3, True, wait_for_result=True)

    assert "9876" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,args,command",
    [
        ("arm", ("1234", [1], 0), SatelWriteCommand.PARTITIONS_ARM_MODE_0),
        ("arm", ("1234", [1], 1), SatelWriteCommand.PARTITIONS_ARM_MODE_1),
        ("arm", ("1234", [1], 2), SatelWriteCommand.PARTITIONS_ARM_MODE_2),
        ("arm", ("1234", [1], 3), SatelWriteCommand.PARTITIONS_ARM_MODE_3),
        ("disarm", ("1234", [1]), SatelWriteCommand.PARTITIONS_DISARM),
        (
            "clear_alarm",
            ("1234", [1]),
            SatelWriteCommand.PARTITIONS_CLEAR_ALARM,
        ),
    ],
)
async def test_partition_control_waits_for_result(
    satel, mock_queue, method, args, command
):
    mock_queue.add_message.return_value = _make_result_message(
        SatelResultCode.COMMAND_ACCEPTED
    )

    result = await getattr(satel, method)(*args, wait_for_result=True)

    assert isinstance(result, SatelCommandResult)
    assert result.is_success
    sent_msg, wait_for_result = mock_queue.add_message.await_args.args
    assert sent_msg.cmd is command
    assert wait_for_result is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,args",
    [
        ("arm", ("1234", [1], 0)),
        ("disarm", ("1234", [1])),
        ("clear_alarm", ("1234", [1])),
    ],
)
async def test_partition_control_wait_for_result_rejected(
    satel, mock_queue, method, args
):
    mock_queue.add_message.return_value = _make_result_message(
        SatelResultCode.NO_ACCESS
    )

    with pytest.raises(SatelCommandRejectedError) as err:
        await getattr(satel, method)(*args, wait_for_result=True)

    assert err.value.result.code == SatelResultCode.NO_ACCESS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,args",
    [
        ("arm", ("1234", [1], 0)),
        ("disarm", ("1234", [1])),
        ("clear_alarm", ("1234", [1])),
    ],
)
async def test_partition_control_wait_for_result_no_response(
    satel, mock_queue, method, args
):
    mock_queue.add_message.return_value = None

    with pytest.raises(SatelNoResponseError):
        await getattr(satel, method)(*args, wait_for_result=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,args",
    [
        ("arm", ([1], 0)),
        ("disarm", ([1],)),
        ("clear_alarm", ([1],)),
        ("set_output", (1, True)),
    ],
)
@pytest.mark.parametrize(
    "code",
    [
        "123",
        "12345678901234567",
    ],
)
async def test_control_actions_reject_invalid_user_code_without_sending(
    satel: AsyncSatel, mock_queue, method, args, code
):
    with pytest.raises(ValueError, match="between 4 and 16 characters"):
        await getattr(satel, method)(code, *args)

    mock_queue.add_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [-1, 4])
async def test_arm_rejects_invalid_mode_without_sending(
    satel: AsyncSatel, mock_queue, mode
):
    with pytest.raises(ValueError, match="mode must be between 0 and 3"):
        await satel.arm("1234", [1], mode)

    mock_queue.add_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "zone_number, payload, expected",
    [
        (1, bytearray([1, 0x00, 0x96]), 20.0),
        (1, bytearray([1, 0xFF, 0xFF]), None),
        (1, None, None),
    ],
)
async def test_read_temperature_returns_expected_value(
    satel, mock_queue, zone_number, payload, expected
):
    mock_queue.add_message.return_value = (
        _typed_message(SatelReadCommand.ZONE_TEMPERATURE, payload)
        if payload is not None
        else None
    )

    result = await satel.read_temperature(zone_number)

    assert result == expected
    mock_queue.add_message.assert_awaited_once()
    sent_msg = mock_queue.add_message.await_args.args[0]
    assert sent_msg.cmd is SatelReadCommand.ZONE_TEMPERATURE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "side_effect, expected",
    [
        ([20.0, 21.5], {1: 20.0, 2: 21.5}),
        ([20.0, None], {1: 20.0, 2: None}),
    ],
)
async def test_read_temperatures_returns_expected_values(satel, side_effect, expected):
    satel.read_temperature = AsyncMock(side_effect=side_effect)

    result = await satel.read_temperatures([1, 2])

    assert result == expected


@pytest.mark.asyncio
async def test_read_zone_info_returns_zone_info(satel, mock_queue):
    response = _typed_message(
        SatelReadCommand.READ_DEVICE_NAME,
        bytearray([0x05, 0x01, 0x2A])
        + bytearray(b"Front Door      ")
        + bytearray([0x03]),
    )
    mock_queue.add_message.return_value = response

    result = await satel.read_zone_info(1)

    assert response.data_type is SatelZoneInfo
    assert result == response.data

    mock_queue.add_message.assert_awaited_once()
    sent_msg = mock_queue.add_message.await_args.args[0]
    assert sent_msg.cmd is SatelReadCommand.READ_DEVICE_NAME
    assert sent_msg.msg_data == bytearray([0x05, 0x01])


@pytest.mark.asyncio
async def test_read_partition_info_returns_partition_info(satel, mock_queue):
    response = _typed_message(
        SatelReadCommand.READ_DEVICE_NAME,
        bytearray([0x10, 0x01, 0x03])
        + bytearray(b"Ground Floor    ")
        + bytearray([0x02]),
    )
    mock_queue.add_message.return_value = response

    result = await satel.read_partition_info(1)

    assert isinstance(result, SatelPartitionInfo)
    assert result == response.data

    mock_queue.add_message.assert_awaited_once()
    sent_msg = mock_queue.add_message.await_args.args[0]
    assert sent_msg.cmd is SatelReadCommand.READ_DEVICE_NAME
    assert sent_msg.msg_data == bytearray([0x10, 0x01])


@pytest.mark.asyncio
async def test_read_zone_info_encodes_zone_256_as_zero(satel, mock_queue):
    response = _typed_message(
        SatelReadCommand.READ_DEVICE_NAME,
        bytearray([0x05, 0x00, 0x2A])
        + bytearray(b"Top Floor       ")
        + bytearray([0x00]),
    )
    mock_queue.add_message.return_value = response

    result = await satel.read_zone_info(256)

    assert result is not None
    assert result.device_number == 256
    assert result.partition_assignment is None

    sent_msg = mock_queue.add_message.await_args.args[0]
    assert sent_msg.msg_data == bytearray([0x05, 0x00])


@pytest.mark.asyncio
async def test_read_output_info_returns_output_info(satel, mock_queue):
    response = _typed_message(
        SatelReadCommand.READ_DEVICE_NAME,
        bytearray([0x04, 0x01, 0x10]) + bytearray(b"Output 1        "),
    )
    mock_queue.add_message.return_value = response

    result = await satel.read_output_info(1)

    assert response.data_type is SatelOutputInfo
    assert result == response.data

    mock_queue.add_message.assert_awaited_once()
    sent_msg = mock_queue.add_message.await_args.args[0]
    assert sent_msg.cmd is SatelReadCommand.READ_DEVICE_NAME
    assert sent_msg.msg_data == bytearray([0x04, 0x01])


@pytest.mark.asyncio
async def test_read_output_info_encodes_output_256_as_zero(satel, mock_queue):
    response = _typed_message(
        SatelReadCommand.READ_DEVICE_NAME,
        bytearray([0x04, 0x00, 0x10]) + bytearray(b"Bell            "),
    )
    mock_queue.add_message.return_value = response

    result = await satel.read_output_info(256)

    assert result is not None
    assert result.device_number == 256

    sent_msg = mock_queue.add_message.await_args.args[0]
    assert sent_msg.msg_data == bytearray([0x04, 0x00])


@pytest.mark.asyncio
async def test_read_partition_info_returns_none_without_response(satel, mock_queue):
    mock_queue.add_message.return_value = None

    result = await satel.read_partition_info(1)

    assert result is None


@pytest.mark.asyncio
async def test_read_panel_info_returns_panel_info(satel, mock_queue):
    response = _typed_message(
        SatelReadCommand.INTEGRA_VERSION,
        bytearray([72]) + bytearray(b"12120230221") + bytearray([0x00, 0xFF]),
    )
    mock_queue.add_message.return_value = response

    result = await satel.read_panel_info()

    assert result == response.data

    mock_queue.add_message.assert_awaited_once()
    sent_msg = mock_queue.add_message.await_args.args[0]
    assert sent_msg.cmd is SatelReadCommand.INTEGRA_VERSION


@pytest.mark.asyncio
async def test_read_panel_info_returns_unknown_model_for_unknown_type(
    satel, mock_queue, caplog
):
    mock_queue.add_message.return_value = _typed_message(
        SatelReadCommand.INTEGRA_VERSION,
        bytearray([99]) + bytearray(b"12120230221") + bytearray([0x00, 0x00]),
    )

    with caplog.at_level(logging.WARNING):
        result = await satel.read_panel_info()

    assert result is not None
    assert result.type_code == 99
    assert result.model is None
    assert result.settings_stored_in_flash is False
    assert "Unknown INTEGRA panel type code: 99" in caplog.text


@pytest.mark.asyncio
async def test_read_panel_info_returns_none_without_panel_response(satel, mock_queue):
    mock_queue.add_message.return_value = None

    result = await satel.read_panel_info()

    assert result is None


@pytest.mark.asyncio
async def test_read_panel_info_rejects_unexpected_panel_response(satel, mock_queue):
    mock_queue.add_message.return_value = SatelReadMessage(
        SatelReadCommand.READ_DEVICE_NAME, bytearray()
    )

    with pytest.raises(SatelUnexpectedResponseError, match="Unexpected response type"):
        await satel.read_panel_info()


@pytest.mark.asyncio
async def test_read_panel_info_rejects_wrong_typed_data(satel, mock_queue):
    mock_queue.add_message.return_value = _typed_message(
        SatelReadCommand.MODULE_VERSION,
        bytearray(b"12320120527") + bytearray([0b0000_0101]),
    )

    with pytest.raises(
        SatelUnexpectedResponseError,
        match=("expected SatelPanelInfo, got SatelCommunicationModuleInfo"),
    ):
        await satel.read_panel_info()


@pytest.mark.asyncio
async def test_read_communication_module_info_returns_module_info(satel, mock_queue):
    response = _typed_message(
        SatelReadCommand.MODULE_VERSION,
        bytearray(b"12320120527") + bytearray([0b0000_0101]),
    )
    mock_queue.add_message.return_value = response

    result = await satel.read_communication_module_info()

    assert result == response.data

    mock_queue.add_message.assert_awaited_once()
    sent_msg = mock_queue.add_message.await_args.args[0]
    assert sent_msg.cmd is SatelReadCommand.MODULE_VERSION


@pytest.mark.asyncio
async def test_read_communication_module_info_returns_none_without_module_response(
    satel, mock_queue
):
    mock_queue.add_message.return_value = None

    result = await satel.read_communication_module_info()

    assert result is None


@pytest.mark.asyncio
async def test_read_communication_module_info_rejects_unexpected_response(
    satel, mock_queue
):
    mock_queue.add_message.return_value = SatelReadMessage(
        SatelReadCommand.READ_DEVICE_NAME, bytearray()
    )

    with pytest.raises(SatelUnexpectedResponseError, match="Unexpected response type"):
        await satel.read_communication_module_info()


@pytest.mark.asyncio
async def test_read_temperature_rejects_unexpected_response_type(satel, mock_queue):
    mock_queue.add_message.return_value = SatelReadMessage(
        SatelReadCommand.READ_DEVICE_NAME, bytearray()
    )

    with pytest.raises(SatelUnexpectedResponseError, match="Unexpected response type"):
        await satel.read_temperature(1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,args",
    [
        ("read_temperature", (0,)),
        ("read_zone_info", (0,)),
        ("read_output_info", (0,)),
    ],
)
async def test_single_device_reads_reject_invalid_device_number(satel, method, args):
    with pytest.raises(ValueError, match="device_number must be between 1 and 256"):
        await getattr(satel, method)(*args)


@pytest.mark.asyncio
async def test_read_zone_info_returns_none_without_response(satel, mock_queue):
    mock_queue.add_message.return_value = None

    result = await satel.read_zone_info(1)

    assert result is None


@pytest.mark.asyncio
async def test_read_output_info_returns_none_without_response(satel, mock_queue):
    mock_queue.add_message.return_value = None

    result = await satel.read_output_info(1)

    assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,args",
    [
        ("read_partition_info", (1,)),
        ("read_zone_info", (1,)),
        ("read_output_info", (1,)),
        ("read_panel_info", ()),
    ],
)
async def test_read_method_raises_for_rejected_result(satel, mock_queue, method, args):
    mock_queue.add_message.return_value = _make_result_message(
        SatelResultCode.OTHER_ERROR
    )

    with pytest.raises(SatelCommandRejectedError) as exc_info:
        await getattr(satel, method)(*args)

    assert exc_info.value.result.code is SatelResultCode.OTHER_ERROR


@pytest.mark.asyncio
async def test_read_method_rejects_success_result_without_data(satel, mock_queue):
    mock_queue.add_message.return_value = _make_result_message(SatelResultCode.OK)

    with pytest.raises(SatelUnexpectedResponseError, match="Unexpected response type"):
        await satel.read_panel_info()


@pytest.mark.asyncio
async def test_read_zone_info_rejects_unexpected_response_type(satel, mock_queue):
    mock_queue.add_message.return_value = SatelReadMessage(
        SatelReadCommand.READ_DEVICE_NAME, bytearray([0x01])
    )

    with pytest.raises(SatelUnexpectedResponseError, match="Unexpected response type"):
        await satel.read_zone_info(1)


@pytest.mark.asyncio
async def test_read_output_info_rejects_unexpected_response_type(satel, mock_queue):
    mock_queue.add_message.return_value = SatelReadMessage(
        SatelReadCommand.READ_DEVICE_NAME, bytearray([0x01])
    )

    with pytest.raises(SatelUnexpectedResponseError, match="Unexpected response type"):
        await satel.read_output_info(1)


@pytest.mark.asyncio
async def test_read_partition_info_rejects_unexpected_response_type(satel, mock_queue):
    mock_queue.add_message.return_value = SatelReadMessage(
        SatelReadCommand.READ_DEVICE_NAME, bytearray([0x01])
    )

    with pytest.raises(SatelUnexpectedResponseError, match="Unexpected response type"):
        await satel.read_partition_info(1)


@pytest.mark.asyncio
async def test_read_zone_info_rejects_zone_mismatch(satel, mock_queue):
    mock_queue.add_message.return_value = _typed_message(
        SatelReadCommand.READ_DEVICE_NAME,
        bytearray([0x05, 0x02, 0x2A])
        + bytearray(b"Front Door      ")
        + bytearray([0x03]),
    )

    with pytest.raises(ValueError, match="Zone info response zone mismatch"):
        await satel.read_zone_info(1)


@pytest.mark.asyncio
async def test_read_output_info_rejects_output_mismatch(satel, mock_queue):
    mock_queue.add_message.return_value = _typed_message(
        SatelReadCommand.READ_DEVICE_NAME,
        bytearray([0x04, 0x02, 0x10]) + bytearray(b"Output 2        "),
    )

    with pytest.raises(ValueError, match="Output info response output mismatch"):
        await satel.read_output_info(1)


@pytest.mark.asyncio
async def test_read_partition_info_rejects_partition_mismatch(satel, mock_queue):
    mock_queue.add_message.return_value = _typed_message(
        SatelReadCommand.READ_DEVICE_NAME,
        bytearray([0x10, 0x02, 0x03])
        + bytearray(b"Ground Floor    ")
        + bytearray([0x02]),
    )

    with pytest.raises(ValueError, match="Partition info response partition mismatch"):
        await satel.read_partition_info(1)


@pytest.mark.asyncio
async def test_read_partition_info_rejects_invalid_partition_number(satel) -> None:
    with pytest.raises(ValueError, match="partition_id must be between 1 and 32"):
        await satel.read_partition_info(0)

    with pytest.raises(ValueError, match="partition_id must be between 1 and 32"):
        await satel.read_partition_info(33)


@pytest.mark.asyncio
async def test_close_cancels_tasks(satel):
    reading_task = asyncio.create_task(asyncio.sleep(999))
    keepalive_task = asyncio.create_task(asyncio.sleep(999))
    satel._running_tasks = {reading_task, keepalive_task}

    await satel.close()

    assert not satel._running_tasks
    assert reading_task.cancelled()
    assert keepalive_task.cancelled()
    satel._connection.close.assert_awaited_once()
    satel._queue.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_read_data_exception_returns_none(satel):
    satel._connection.read_frame.side_effect = Exception("boom")

    result = await satel._read_data()
    assert result is None


@pytest.mark.asyncio
async def test_start_starts_background_tasks(satel):
    satel._watch_connection_stopped = AsyncMock()
    satel._reading_loop = AsyncMock()
    satel._keepalive_loop = AsyncMock()
    satel._monitor_reconnection_loop = AsyncMock()
    satel._start_task = MagicMock(side_effect=lambda coro: asyncio.create_task(coro))
    satel.start_monitoring = AsyncMock()

    await satel.start(enable_monitoring=True)

    assert satel._start_task.call_count == 4
    satel._connection.ensure_connected.assert_awaited_once()
    satel._queue.start.assert_awaited_once()
    satel.start_monitoring.assert_awaited()


@pytest.mark.asyncio
async def test_start_skips_monitoring(satel):
    satel._watch_connection_stopped = AsyncMock()
    satel._reading_loop = AsyncMock()
    satel._keepalive_loop = AsyncMock()
    satel._start_task = MagicMock(side_effect=lambda coro: asyncio.create_task(coro))
    satel.start_monitoring = AsyncMock()

    await satel.start(enable_monitoring=False)

    assert satel._start_task.call_count == 3
    satel.start_monitoring.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_returns_early_when_initial_connection_fails(satel, mock_queue):
    satel._connection.ensure_connected.side_effect = SatelConnectionStoppedError
    satel._connection.stopped = True
    satel._start_task = MagicMock()
    satel.start_monitoring = AsyncMock()

    await satel.start(enable_monitoring=True)

    satel._start_task.assert_not_called()
    mock_queue.stop.assert_not_awaited()
    mock_queue.start.assert_not_awaited()
    satel.start_monitoring.assert_not_awaited()


@pytest.mark.asyncio
async def test_keepalive_loop_stops_when_connection_closes(
    satel, mock_connection, fake_sleep_factory
):
    fake_sleep_factory(
        lambda sleep_count, _duration, _loop: (
            setattr(mock_connection, "stopped", True) if sleep_count == 2 else None
        )
    )
    satel._send_data_and_wait = AsyncMock(return_value=MagicMock())

    await satel._keepalive_loop()

    satel._send_data_and_wait.assert_called_once()


@pytest.mark.asyncio
async def test_keepalive_loop_sends_rtc_and_status_query(
    satel, mock_connection, fake_sleep_factory
):
    fake_sleep_factory(
        lambda sleep_count, _duration, _loop: (
            setattr(mock_connection, "stopped", True) if sleep_count == 2 else None
        )
    )
    satel._send_data_and_wait = AsyncMock(return_value=MagicMock())

    await satel._keepalive_loop()

    satel._send_data_and_wait.assert_called_once()
    keepalive_message = satel._send_data_and_wait.await_args.args[0]
    assert satel._send_data_and_wait.await_args.args[1] is SatelRtcAndStatus
    assert keepalive_message.cmd is SatelReadCommand.RTC_AND_STATUS
    assert keepalive_message.msg_data == bytearray()


@pytest.mark.asyncio
async def test_keepalive_timeout_marks_same_connection_as_lost(
    satel, mock_connection, monkeypatch, caplog
):
    monkeypatch.setattr("satel_integra.satel_integra.KEEPALIVE_INTERVAL", 0.01)
    satel._send_data_and_wait = AsyncMock(side_effect=[None, asyncio.CancelledError()])

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(asyncio.CancelledError):
            await satel._keepalive_loop()

    assert "Keepalive timed out on current connection" in caplog.text
    mock_connection.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_keepalive_timeout_does_not_disconnect_reconnected_session(
    satel, mock_connection, monkeypatch, caplog
):
    monkeypatch.setattr("satel_integra.satel_integra.KEEPALIVE_INTERVAL", 0.01)
    calls = 0

    async def timeout_after_reconnect(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise asyncio.CancelledError
        mock_connection.generation = 2
        return None

    satel._send_data_and_wait = AsyncMock(side_effect=timeout_after_reconnect)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(asyncio.CancelledError):
            await satel._keepalive_loop()

    assert (
        "Ignoring stale keepalive timeout from connection generation 1" in caplog.text
    )
    mock_connection.disconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_keepalive_loop_logs_late_wakeup(
    satel, mock_connection, monkeypatch, caplog, fake_loop
):
    sleep_calls = []

    async def fake_sleep(duration):
        sleep_calls.append(duration)
        fake_loop.now += duration + 2
        if len(sleep_calls) > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr("satel_integra.satel_integra.asyncio.sleep", fake_sleep)
    satel._send_data_and_wait = AsyncMock(return_value=MagicMock())

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(asyncio.CancelledError):
            await satel._keepalive_loop()

    assert "Keepalive woke up 2.000s after the idle deadline" in caplog.text


@pytest.mark.asyncio
async def test_keepalive_loop_waits_full_interval_while_disconnected(
    satel, mock_connection, fake_sleep_factory
):
    mock_connection.connected = False
    sleep_calls = fake_sleep_factory(
        lambda sleep_count, _duration, _loop: (
            setattr(mock_connection, "connected", True) if sleep_count == 2 else None
        )
    )
    satel._send_data_and_wait = AsyncMock(side_effect=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await satel._keepalive_loop()

    assert sleep_calls == [5, 5]
    satel._send_data_and_wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_keepalive_loop_skips_send_when_recent_outbound_traffic_seen(
    satel, mock_connection, fake_sleep_factory
):
    sleep_calls = fake_sleep_factory(
        lambda sleep_count, _duration, loop: (
            setattr(mock_connection, "last_outbound_activity", loop.now)
            if sleep_count == 1
            else None
        )
    )
    satel._send_data_and_wait = AsyncMock(side_effect=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await satel._keepalive_loop()

    assert sleep_calls == [5, 5]
    satel._send_data_and_wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_keepalive_loop_logs_why_send_was_skipped(
    satel, mock_connection, caplog, fake_sleep_factory
):
    fake_sleep_factory(
        lambda sleep_count, _duration, loop: (
            setattr(mock_connection, "stopped", True)
            if sleep_count == 2
            else setattr(mock_connection, "last_outbound_activity", loop.now)
        )
    )

    with caplog.at_level(logging.DEBUG):
        await satel._keepalive_loop()

    assert "Keepalive skipped because outbound activity was seen" in caplog.text


@pytest.mark.asyncio
async def test_reading_loop_processes_message(satel, mock_connection):
    msg = MagicMock()
    msg.cmd = 1

    satel._connection.ensure_connected = AsyncMock(
        side_effect=[None, SatelConnectionStoppedError]
    )
    satel._read_data = AsyncMock(return_value=msg)

    cmd_handler = MagicMock()

    satel._message_handlers = {1: cmd_handler}

    await satel._reading_loop()

    cmd_handler.assert_called_once()


@pytest.mark.asyncio
async def test_reading_loop_continues_after_handler_error(satel, mock_connection):
    first = MagicMock()
    first.cmd = 1
    second = MagicMock()
    second.cmd = 1

    satel._connection.ensure_connected = AsyncMock(
        side_effect=[None, None, SatelConnectionStoppedError]
    )
    satel._read_data = AsyncMock(side_effect=[first, second])

    cmd_handler = MagicMock(side_effect=[ValueError("bad frame"), None])
    satel._message_handlers = {1: cmd_handler}

    await satel._reading_loop()

    assert cmd_handler.call_count == 2


@pytest.mark.asyncio
async def test_reading_loop_forwards_expected_read_response_to_queue(
    satel, mock_connection, mock_queue
):
    msg = MagicMock()
    msg.cmd = SatelReadCommand.RTC_AND_STATUS

    satel._connection.ensure_connected = AsyncMock(
        side_effect=[None, SatelConnectionStoppedError]
    )
    satel._read_data = AsyncMock(return_value=msg)

    await satel._reading_loop()

    mock_queue.on_message_received.assert_called_once_with(msg)


@pytest.mark.asyncio
async def test_reading_loop_forwards_unexpected_read_response_to_queue_filter(
    satel, mock_connection, mock_queue
):
    msg = MagicMock()
    msg.cmd = SatelReadCommand.ZONES_VIOLATED

    satel._connection.ensure_connected = AsyncMock(
        side_effect=[None, SatelConnectionStoppedError]
    )
    satel._read_data = AsyncMock(return_value=msg)

    await satel._reading_loop()

    mock_queue.on_message_received.assert_called_once_with(msg)


@pytest.mark.asyncio
async def test_reading_loop_stops_when_reconnect_closes_connection(
    satel, mock_connection
):
    satel._connection.ensure_connected = AsyncMock(
        side_effect=SatelConnectionStoppedError
    )
    satel._read_data = AsyncMock()

    await satel._reading_loop()

    satel._read_data.assert_not_awaited()
    satel._queue.stop.assert_not_awaited()


@pytest.mark.asyncio
async def test_monitor_reconnection_loop_exits_when_connection_closes(satel):
    satel._connection.wait_reconnected.side_effect = SatelConnectionStoppedError
    satel._connection.stopped = True
    satel.start_monitoring = AsyncMock()

    await satel._monitor_reconnection_loop()

    satel._queue.stop.assert_not_awaited()
    satel.start_monitoring.assert_not_awaited()


@pytest.mark.asyncio
async def test_watch_connection_stopped_stops_queue_and_tasks(satel):
    satel._running_tasks = {
        asyncio.create_task(asyncio.sleep(999)),
        asyncio.create_task(asyncio.sleep(999)),
        asyncio.create_task(asyncio.sleep(999)),
    }

    await satel._watch_connection_stopped()

    assert not satel._running_tasks
    satel._queue.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_passes_verify_connection_flag(satel, mock_connection):
    await satel.connect(verify_connection=False)

    mock_connection.connect.assert_awaited_once_with(
        verify_connection=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc_type",
    [
        SatelConnectFailedError,
        SatelPanelBusyError,
        SatelConnectionInitializationError,
        SatelConnectionStoppedError,
    ],
)
async def test_connect_returns_false_in_compat_mode_for_connect_exceptions(
    satel, mock_connection, exc_type
):
    mock_connection.connect.side_effect = exc_type("boom")

    result = await satel.connect()

    assert result is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc_type",
    [
        SatelConnectFailedError,
        SatelPanelBusyError,
        SatelConnectionInitializationError,
        SatelConnectionStoppedError,
    ],
)
async def test_connect_raises_in_strict_mode_for_connect_exceptions(
    satel, mock_connection, exc_type
):
    mock_connection.connect.side_effect = exc_type("boom")

    with pytest.raises(exc_type, match="boom"):
        await satel.connect(raise_exceptions=True)


def test_add_connection_status_callback_forwards_to_connection(satel, mock_connection):
    callback = MagicMock()
    unsubscribe = MagicMock()
    mock_connection.add_connection_state_callback.return_value = unsubscribe

    result = satel.add_connection_status_callback(callback)

    mock_connection.add_connection_state_callback.assert_any_call(callback)
    assert result is unsubscribe


def test_connection_state_changed_logs_lost_once(satel, mock_connection, caplog):
    mock_connection.connected = False
    mock_connection.stopped = False

    with caplog.at_level(logging.INFO):
        satel._connection_state_changed()
        satel._connection_state_changed()

    assert caplog.text.count("Connection to Satel Integra panel lost") == 1


def test_connection_state_changed_logs_restored_once_after_loss(
    satel, mock_connection, caplog
):
    mock_connection.connected = False
    satel._connection_state_changed()

    mock_connection.connected = True
    with caplog.at_level(logging.INFO):
        satel._connection_state_changed()
        satel._connection_state_changed()

    assert caplog.text.count("Connection to Satel Integra panel restored") == 1


def test_connection_state_changed_does_not_log_restored_without_prior_loss(
    satel, mock_connection, caplog
):
    mock_connection.connected = True

    with caplog.at_level(logging.INFO):
        satel._connection_state_changed()

    assert "Connection to Satel Integra panel restored" not in caplog.text


def test_connection_state_changed_does_not_log_during_shutdown(
    satel, mock_connection, caplog
):
    satel._closing = True
    mock_connection.connected = False

    with caplog.at_level(logging.INFO):
        satel._connection_state_changed()

    assert "Connection to Satel Integra panel lost" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type", [SatelMonitoringStartError, asyncio.CancelledError]
)
async def test_start_rolls_back_when_monitoring_fails(satel, error_type):
    error = error_type()
    # Dispose of background coroutines without scheduling real tasks.
    satel._start_task = MagicMock(side_effect=lambda coro: coro.close())
    satel.start_monitoring = AsyncMock(side_effect=error)
    satel.close = AsyncMock()

    with pytest.raises(error_type) as exc_info:
        await satel.start(enable_monitoring=True)

    satel.close.assert_awaited_once()
    assert exc_info.value is error
