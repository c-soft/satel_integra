import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from satel_integra.commands import SatelReadCommand
from satel_integra.exceptions import (
    SatelConnectFailedError,
    SatelConnectionInitializationError,
    SatelConnectionStoppedError,
    SatelPanelBusyError,
    SatelUnexpectedResponseError,
)
from satel_integra.messages import (
    SatelIntegraVersionReadMessage,
    SatelModuleVersionReadMessage,
    SatelOutputInfoReadMessage,
    SatelPartitionInfoReadMessage,
    SatelReadMessage,
    SatelZoneInfoReadMessage,
    SatelZoneTemperatureReadMessage,
)
from satel_integra.models import SatelPartitionInfo
from satel_integra.satel_integra import AlarmState, AsyncSatel


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
def satel(monkeypatch, mock_connection, mock_queue):
    monkeypatch.setattr(
        "satel_integra.satel_integra.SatelConnection", lambda *a, **kw: mock_connection
    )
    monkeypatch.setattr(
        "satel_integra.satel_integra.SatelMessageQueue", lambda send: mock_queue
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
    mock_msg = MagicMock()
    mock_msg.msg_data = b"\xff"
    mock_queue.add_message.return_value = mock_msg

    await satel.start_monitoring()

    mock_queue.add_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_monitoring_rejected(satel, mock_queue, caplog):
    mock_msg = MagicMock()
    mock_msg.msg_data = b"\x00"
    mock_queue.add_message.return_value = mock_msg

    await satel.start_monitoring()

    assert "Monitoring not accepted" in caplog.text


def test_zones_violated_callback(satel):
    msg = MagicMock()
    msg.get_active_bits.return_value = [1]

    called = {}
    satel.register_callbacks(zone_changed_callback=lambda status: called.update(status))

    satel._zones_violated(msg)

    assert called == {1: 1, 2: 0}
    assert satel.violated_zones == [1]


def test_outputs_changed_callback(satel):
    msg = MagicMock()
    msg.get_active_bits.return_value = [4]

    called = {}
    satel.register_callbacks(
        output_changed_callback=lambda status: called.update(status)
    )

    satel._outputs_changed(msg)

    assert called == {3: 0, 4: 1}
    assert satel.violated_outputs == [4]


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

    assert satel.partition_states[AlarmState.ARMED_MODE0] == [1]
    assert called


def test_command_result_ok(satel, caplog):
    msg = MagicMock()
    msg.msg_data = [b"\xff"]

    with caplog.at_level(logging.DEBUG):
        satel._command_result(msg)

    assert "OK" in caplog.text


def test_command_result_user_code_not_found(satel, caplog):
    msg = MagicMock()
    msg.msg_data = [b"\x01"]

    with caplog.at_level(logging.DEBUG):
        satel._command_result(msg)
    assert "User code not found" in caplog.text


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
    await getattr(satel, method)(*args)
    mock_queue.add_message.assert_awaited()


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
        SatelZoneTemperatureReadMessage(SatelReadCommand.ZONE_TEMPERATURE, payload)
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
    response = SatelZoneInfoReadMessage(
        SatelReadCommand.READ_DEVICE_NAME,
        bytearray([0x05, 0x01, 0x2A])
        + bytearray(b"Front Door      ")
        + bytearray([0x03]),
    )
    mock_queue.add_message.return_value = response

    result = await satel.read_zone_info(1)

    assert isinstance(response, SatelZoneInfoReadMessage)
    assert result == response.data

    mock_queue.add_message.assert_awaited_once()
    sent_msg = mock_queue.add_message.await_args.args[0]
    assert sent_msg.cmd is SatelReadCommand.READ_DEVICE_NAME
    assert sent_msg.msg_data == bytearray([0x05, 0x01])


@pytest.mark.asyncio
async def test_read_partition_info_returns_partition_info(satel, mock_queue):
    response = SatelPartitionInfoReadMessage(
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
    response = SatelZoneInfoReadMessage(
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
    response = SatelOutputInfoReadMessage(
        SatelReadCommand.READ_DEVICE_NAME,
        bytearray([0x04, 0x01, 0x10]) + bytearray(b"Output 1        "),
    )
    mock_queue.add_message.return_value = response

    result = await satel.read_output_info(1)

    assert isinstance(response, SatelOutputInfoReadMessage)
    assert result == response.data

    mock_queue.add_message.assert_awaited_once()
    sent_msg = mock_queue.add_message.await_args.args[0]
    assert sent_msg.cmd is SatelReadCommand.READ_DEVICE_NAME
    assert sent_msg.msg_data == bytearray([0x04, 0x01])


@pytest.mark.asyncio
async def test_read_output_info_encodes_output_256_as_zero(satel, mock_queue):
    response = SatelOutputInfoReadMessage(
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
    response = SatelIntegraVersionReadMessage(
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
    mock_queue.add_message.return_value = SatelIntegraVersionReadMessage(
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
async def test_read_communication_module_info_returns_module_info(satel, mock_queue):
    response = SatelModuleVersionReadMessage(
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
async def test_read_partition_info_returns_none_for_unavailable_partition_result(
    satel, mock_queue
):
    mock_queue.add_message.return_value = SatelReadMessage(
        SatelReadCommand.RESULT, bytearray([0x08])
    )

    result = await satel.read_partition_info(1)

    assert result is None


@pytest.mark.asyncio
async def test_read_zone_info_returns_none_for_unavailable_zone_result(
    satel, mock_queue
):
    mock_queue.add_message.return_value = SatelReadMessage(
        SatelReadCommand.RESULT, bytearray([0x08])
    )

    result = await satel.read_zone_info(1)

    assert result is None


@pytest.mark.asyncio
async def test_read_output_info_returns_none_for_unavailable_output_result(
    satel, mock_queue
):
    mock_queue.add_message.return_value = SatelReadMessage(
        SatelReadCommand.RESULT, bytearray([0x08])
    )

    result = await satel.read_output_info(1)

    assert result is None


@pytest.mark.asyncio
async def test_read_panel_info_returns_none_for_result_response(satel, mock_queue):
    mock_queue.add_message.return_value = SatelReadMessage(
        SatelReadCommand.RESULT, bytearray([0x08])
    )

    result = await satel.read_panel_info()

    assert result is None


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
    mock_queue.add_message.return_value = SatelZoneInfoReadMessage(
        SatelReadCommand.READ_DEVICE_NAME,
        bytearray([0x05, 0x02, 0x2A])
        + bytearray(b"Front Door      ")
        + bytearray([0x03]),
    )

    with pytest.raises(ValueError, match="Zone info response zone mismatch"):
        await satel.read_zone_info(1)


@pytest.mark.asyncio
async def test_read_output_info_rejects_output_mismatch(satel, mock_queue):
    mock_queue.add_message.return_value = SatelOutputInfoReadMessage(
        SatelReadCommand.READ_DEVICE_NAME,
        bytearray([0x04, 0x02, 0x10]) + bytearray(b"Output 2        "),
    )

    with pytest.raises(ValueError, match="Output info response output mismatch"):
        await satel.read_output_info(1)


@pytest.mark.asyncio
async def test_read_partition_info_rejects_partition_mismatch(satel, mock_queue):
    mock_queue.add_message.return_value = SatelPartitionInfoReadMessage(
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


def test_add_connection_status_callback_forwards_to_transport(satel, mock_connection):
    callback = MagicMock()

    satel.add_connection_status_callback(callback)

    mock_connection.add_connection_state_callback.assert_any_call(callback)


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
