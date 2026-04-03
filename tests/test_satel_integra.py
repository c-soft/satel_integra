import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from satel_integra.exceptions import (
    SatelConnectionInitializationError,
    SatelConnectionStoppedError,
    SatelFrameDecodeError,
    SatelMonitoringRejectedError,
    SatelResponseTimeoutError,
    SatelTransportDisconnectedError,
)
from satel_integra.satel_integra import AlarmState, AsyncSatel


@pytest.fixture
def mock_connection():
    conn = AsyncMock()
    conn.connected = True
    conn.stopped = False
    conn.ensure_connected = AsyncMock(return_value=True)
    conn.wait_reconnected = AsyncMock(return_value=True)
    conn.wait_stopped = AsyncMock(return_value=None)
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
    return satel


@pytest.mark.asyncio
async def test_internal_start_monitoring_success(satel, mock_queue):
    mock_msg = MagicMock()
    mock_msg.msg_data = b"\xff"
    mock_queue.add_message.return_value = mock_msg

    await satel._start_monitoring()

    mock_queue.add_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_internal_start_monitoring_rejected_raises(satel, mock_queue):
    mock_msg = MagicMock()
    mock_msg.msg_data = b"\x00"
    mock_queue.add_message.return_value = mock_msg

    with pytest.raises(SatelMonitoringRejectedError, match="Monitoring not accepted"):
        await satel._start_monitoring()


@pytest.mark.asyncio
async def test_internal_start_monitoring_timeout_raises(satel, mock_queue):
    mock_queue.add_message.side_effect = SatelResponseTimeoutError("timeout")

    with pytest.raises(SatelResponseTimeoutError, match="timeout"):
        await satel._start_monitoring()


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
async def test_close_cancels_tasks(satel):
    reading_task = asyncio.create_task(asyncio.sleep(999))
    keepalive_task = asyncio.create_task(asyncio.sleep(999))
    satel._running_tasks = {reading_task, keepalive_task}

    await satel.close()

    assert not satel._running_tasks


@pytest.mark.asyncio
async def test_read_data_exception_raises(satel):
    satel._connection.read_frame.side_effect = Exception("boom")

    with pytest.raises(Exception, match="boom"):
        await satel._read_data()


@pytest.mark.asyncio
async def test_start_starts_background_tasks(satel):
    satel._watch_connection_stopped = AsyncMock()
    satel._reading_loop = AsyncMock()
    satel._keepalive_loop = AsyncMock()
    satel._monitor_reconnection_loop = AsyncMock()
    satel._start_task = MagicMock(side_effect=lambda coro: asyncio.create_task(coro))
    satel._start_monitoring = AsyncMock()

    await satel.start(enable_monitoring=True, raise_exceptions=False)

    assert satel._start_task.call_count == 4
    satel._connection.ensure_connected.assert_awaited_once()
    satel._queue.start.assert_awaited_once()
    satel._start_monitoring.assert_awaited()


@pytest.mark.asyncio
async def test_start_skips_monitoring(satel):
    satel._watch_connection_stopped = AsyncMock()
    satel._reading_loop = AsyncMock()
    satel._keepalive_loop = AsyncMock()
    satel._start_task = MagicMock(side_effect=lambda coro: asyncio.create_task(coro))
    satel._start_monitoring = AsyncMock()

    await satel.start(enable_monitoring=False, raise_exceptions=False)

    assert satel._start_task.call_count == 3
    satel._start_monitoring.assert_not_awaited()


def test_connect_warns_when_raise_exceptions_not_provided(satel, mock_connection):
    with pytest.deprecated_call(match="Calling 'connect' without 'raise_exceptions'"):
        asyncio.run(satel.connect())


@pytest.mark.asyncio
async def test_connect_warns_when_raise_exceptions_false(satel):
    with pytest.deprecated_call(match="Calling 'connect' with raise_exceptions=False"):
        await satel.connect(raise_exceptions=False)


@pytest.mark.asyncio
async def test_connect_raises_when_enabled(satel, mock_connection):
    mock_connection.connect.side_effect = SatelConnectionInitializationError("boom")

    with pytest.raises(SatelConnectionInitializationError, match="boom"):
        await satel.connect(raise_exceptions=True)


@pytest.mark.asyncio
async def test_start_warns_when_raise_exceptions_not_provided(satel):
    with pytest.deprecated_call(match="Calling 'start' without 'raise_exceptions'"):
        await satel.start()


@pytest.mark.asyncio
async def test_start_raises_when_enabled_and_monitoring_setup_fails(satel):
    satel._start_monitoring = AsyncMock(
        side_effect=SatelResponseTimeoutError("timeout")
    )

    with pytest.raises(SatelResponseTimeoutError, match="timeout"):
        await satel.start(enable_monitoring=True, raise_exceptions=True)


@pytest.mark.asyncio
async def test_start_cleans_up_started_runtime_when_monitoring_setup_fails_in_strict_mode(
    satel,
):
    satel._watch_connection_stopped = AsyncMock()
    satel._reading_loop = AsyncMock()
    satel._keepalive_loop = AsyncMock()
    satel._monitor_reconnection_loop = AsyncMock()
    satel._start_task = MagicMock(side_effect=lambda coro: asyncio.create_task(coro))
    satel._cancel_running_tasks = AsyncMock()
    satel._start_monitoring = AsyncMock(
        side_effect=SatelResponseTimeoutError("timeout")
    )

    with pytest.raises(SatelResponseTimeoutError, match="timeout"):
        await satel.start(enable_monitoring=True, raise_exceptions=True)

    assert satel._start_task.call_count == 2
    satel._queue.start.assert_awaited_once()
    satel._queue.stop_processing.assert_awaited_once()
    satel._cancel_running_tasks.assert_awaited_once()
    satel._keepalive_loop.assert_not_awaited()
    satel._monitor_reconnection_loop.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_swallows_monitoring_rejection_in_compat_mode(
    satel, mock_queue, caplog
):
    mock_msg = MagicMock()
    mock_msg.msg_data = b"\x00"
    mock_queue.add_message.return_value = mock_msg

    with pytest.deprecated_call(match="Calling 'start' with raise_exceptions=False"):
        await satel.start(enable_monitoring=True, raise_exceptions=False)

    assert "Monitoring not accepted." in caplog.text


@pytest.mark.asyncio
async def test_start_swallows_monitoring_setup_failure_in_compat_mode(
    satel, mock_queue
):
    mock_queue.add_message.side_effect = SatelResponseTimeoutError("timeout")

    with pytest.deprecated_call(match="Calling 'start' with raise_exceptions=False"):
        await satel.start(enable_monitoring=True, raise_exceptions=False)


@pytest.mark.asyncio
async def test_start_warns_when_raise_exceptions_false(satel):
    with pytest.deprecated_call(match="Calling 'start' with raise_exceptions=False"):
        await satel.start(raise_exceptions=False)


@pytest.mark.asyncio
async def test_start_raises_when_enabled_and_connection_is_stopped(satel):
    satel._connection.ensure_connected.side_effect = SatelConnectionStoppedError("stop")

    with pytest.raises(SatelConnectionStoppedError, match="stop"):
        await satel.start(raise_exceptions=True)


@pytest.mark.asyncio
async def test_start_returns_early_when_initial_connection_fails(satel, mock_queue):
    satel._connection.ensure_connected.side_effect = SatelConnectionStoppedError
    satel._connection.stopped = True
    satel._start_task = MagicMock()
    satel._start_monitoring = AsyncMock()

    await satel.start(enable_monitoring=True, raise_exceptions=False)

    satel._start_task.assert_not_called()
    mock_queue.stop.assert_not_awaited()
    mock_queue.start.assert_not_awaited()
    satel._start_monitoring.assert_not_awaited()


@pytest.mark.asyncio
async def test_keepalive_loop_stops_when_connection_closes(satel, mock_connection):
    satel._keepalive_timeout = 0.01
    satel._send_data = AsyncMock(
        side_effect=lambda *args, **kwargs: setattr(mock_connection, "stopped", True)
    )

    await satel._keepalive_loop()

    satel._send_data.assert_called_once()


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
async def test_reading_loop_retries_after_temporary_disconnect(satel, mock_connection):
    msg = MagicMock()
    msg.cmd = 1

    satel._connection.ensure_connected = AsyncMock(
        side_effect=[None, None, SatelConnectionStoppedError]
    )
    satel._read_data = AsyncMock(
        side_effect=[SatelTransportDisconnectedError("temporary"), msg]
    )
    cmd_handler = MagicMock()
    satel._message_handlers = {1: cmd_handler}

    await satel._reading_loop()

    assert satel._read_data.await_count == 2
    cmd_handler.assert_called_once_with(msg)
    satel._connection.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_reading_loop_closes_on_unexpected_receive_error(satel, mock_connection):
    satel._connection.ensure_connected = AsyncMock(side_effect=[None])
    satel._read_data = AsyncMock(side_effect=SatelFrameDecodeError("bad frame"))

    await satel._reading_loop()

    satel._connection.close.assert_awaited_once()
    satel._queue.stop.assert_awaited_once()


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
    satel._start_monitoring = AsyncMock()

    await satel._monitor_reconnection_loop()

    satel._queue.stop.assert_not_awaited()
    satel._start_monitoring.assert_not_awaited()


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
    await satel.connect(verify_connection=False, raise_exceptions=False)

    mock_connection.connect.assert_awaited_once_with(verify_connection=False)
