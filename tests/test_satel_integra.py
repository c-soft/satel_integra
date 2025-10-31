import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from satel_integra.satel_integra import AlarmState, AsyncSatel


@pytest.fixture
def mock_connection():
    conn = AsyncMock()
    conn.connected = True
    conn.closed = False
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
async def test_close_cancels_tasks(satel):
    satel._reading_task = asyncio.create_task(asyncio.sleep(999))
    satel._keepalive_task = asyncio.create_task(asyncio.sleep(999))

    await satel.close()

    assert satel._reading_task is None
    assert satel._keepalive_task is None


@pytest.mark.asyncio
async def test_read_data_exception_returns_none(satel):
    satel._connection.read_frame.side_effect = Exception("boom")

    result = await satel._read_data()
    assert result is None


@pytest.mark.asyncio
async def test_start_starts_background_tasks(satel):
    satel._reading_task = None
    satel._keepalive_task = None

    satel._reading_loop = AsyncMock()
    satel._keepalive_loop = AsyncMock()
    satel.start_monitoring = AsyncMock()

    await satel.start(enable_monitoring=True)

    # Tasks are created
    assert satel._reading_task is not None
    assert satel._keepalive_task is not None

    # Monitoring called
    satel.start_monitoring.assert_awaited()


@pytest.mark.asyncio
async def test_start_skips_monitoring(satel):
    satel._reading_task = None
    satel._keepalive_task = None

    satel._reading_loop = AsyncMock()
    satel._keepalive_loop = AsyncMock()
    satel.start_monitoring = AsyncMock()

    await satel.start(enable_monitoring=False)

    satel.start_monitoring.assert_not_awaited()


@pytest.mark.asyncio
async def test_keepalive_loop_sends_message(satel):
    satel._keepalive_timeout = 0.01
    satel._send_data = AsyncMock()

    # Close after 1 call
    type(satel).closed = PropertyMock(side_effect=[False, True])

    await satel._keepalive_loop()

    satel._send_data.assert_called_once()


@pytest.mark.asyncio
async def test_reading_loop_processes_message(satel):
    type(satel).closed = PropertyMock(side_effect=[False, True])
    satel._queue.on_message_received = MagicMock()

    msg = MagicMock()
    msg.cmd = 1

    satel._read_data = AsyncMock(side_effect=[msg, None])  # Return one msg then None

    cmd_handler = MagicMock()

    satel._message_handlers = {1: cmd_handler}

    await satel._reading_loop()

    satel._queue.on_message_received.assert_called_once()
    cmd_handler.assert_called_once()
