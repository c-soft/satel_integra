import asyncio
import logging
from unittest.mock import AsyncMock, Mock
import pytest

from satel_integra.queue import SatelMessageQueue, QueuedMessage
from satel_integra.messages import SatelReadMessage, SatelWriteMessage
from satel_integra.commands import SatelReadCommand, SatelWriteCommand


@pytest.fixture
def mock_send_func():
    """Mock async send function."""
    return AsyncMock()


@pytest.fixture
def write_msg():
    """Simple write message fixture."""
    return SatelWriteMessage(
        SatelWriteCommand.PARTITIONS_DISARM, raw_data=bytearray([0x00])
    )


@pytest.fixture
def result_msg():
    """Matching result message fixture."""
    return SatelReadMessage(SatelReadCommand.RESULT, bytearray([0x01]))


@pytest.mark.asyncio
async def test_start_creates_task():
    queue = SatelMessageQueue(mock_send_func)
    queue._process_queue = AsyncMock()

    await queue.start()

    assert queue._process_task is not None
    queue._process_queue.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_already_running(monkeypatch):
    queue = SatelMessageQueue(mock_send_func)
    queue._process_queue = AsyncMock()

    existing_task = Mock()
    queue._process_task = existing_task

    mock_create_task = Mock()
    monkeypatch.setattr("satel_integra.queue.asyncio.create_task", mock_create_task)

    await queue.start()

    mock_create_task.assert_not_called()
    assert queue._process_task is existing_task


@pytest.mark.asyncio
async def test_stop():
    queue = SatelMessageQueue(mock_send_func)

    # Create a dummy task that will never complete
    async def dummy_coro():
        await asyncio.sleep(999)

    task = asyncio.create_task(dummy_coro())

    queue._process_task = task

    await queue.stop()

    assert queue._closed is True
    assert task.cancelled()
    assert queue._process_task is None


@pytest.mark.asyncio
async def test_queued_message_init(write_msg):
    message = QueuedMessage(write_msg, False)

    assert message.return_result is False
    assert message.expected_result_command is SatelReadCommand.RESULT


@pytest.mark.asyncio
async def test_queued_message_init_same_cmd():
    write_msg = SatelWriteMessage(SatelWriteCommand.READ_DEVICE_NAME)
    message = QueuedMessage(write_msg, True)

    assert message.return_result is True
    assert message.expected_result_command is SatelWriteCommand.READ_DEVICE_NAME


@pytest.mark.asyncio
async def test_get_next_message(mock_send_func, write_msg):
    queue = SatelMessageQueue(mock_send_func)

    queued = QueuedMessage(write_msg, False)
    await queue._queue.put(queued)

    result = await queue._get_next_message()

    assert result is not None
    assert result is queued


@pytest.mark.asyncio
async def test_get_next_message_empty_queue(mock_send_func):
    queue = SatelMessageQueue(mock_send_func)

    result = await queue._get_next_message()

    assert result is None


@pytest.mark.asyncio
async def test_stop_cancels_task(mock_send_func, write_msg):
    queue = SatelMessageQueue(mock_send_func)
    await queue.start()
    await queue.stop()
    assert queue._process_task is None
    assert queue._closed is True


@pytest.mark.asyncio
async def test_add_message_wait_for_result(mock_send_func, write_msg, result_msg):
    queue = SatelMessageQueue(mock_send_func)
    await queue.start()

    async def complete_result():
        await asyncio.sleep(0.01)
        queue.on_message_received(result_msg)

    asyncio.create_task(complete_result())

    result = await queue.add_message(write_msg, True)

    assert result is result_msg

    mock_send_func.assert_awaited_once_with(write_msg)


@pytest.mark.asyncio
async def test_add_message_no_wait(mock_send_func, write_msg):
    queue = SatelMessageQueue(mock_send_func)
    await queue.start()
    result = await queue.add_message(write_msg, False)
    await asyncio.sleep(0.05)
    await queue.stop()

    mock_send_func.assert_awaited_once_with(write_msg)

    assert result is None


@pytest.mark.asyncio
async def test_add_message_after_stop_raises(mock_send_func, write_msg):
    queue = SatelMessageQueue(mock_send_func)
    await queue.start()
    await queue.stop()

    with pytest.raises(RuntimeError, match="Queue is stopped"):
        await queue.add_message(write_msg)


@pytest.mark.asyncio
async def test_on_message_received_correct(mock_send_func, write_msg, result_msg):
    queue = SatelMessageQueue(mock_send_func)
    queued = QueuedMessage(write_msg, True)
    queue._current_message = queued

    queue.on_message_received(result_msg)

    assert queued.processed_future.done()


@pytest.mark.asyncio
async def test_on_message_received_commmand_mismatch(
    mock_send_func, result_msg, caplog
):
    caplog.at_level(logging.WARNING)

    queue = SatelMessageQueue(mock_send_func)
    queued = QueuedMessage(SatelWriteMessage(SatelWriteCommand.READ_DEVICE_NAME), True)
    queue._current_message = queued
    queue.on_message_received(result_msg)

    assert "Received result but message expects different result" in caplog.text

    assert not queued.processed_future.done()


@pytest.mark.asyncio
async def test_on_message_received_no_current_message(mock_send_func, result_msg):
    queue = SatelMessageQueue(mock_send_func)
    # No current message set — should simply return
    queue.on_message_received(result_msg)  # No error expected


@pytest.mark.asyncio
async def test_on_message_received_future_already_done(
    mock_send_func, write_msg, result_msg, caplog
):
    caplog.at_level(logging.WARNING)

    queue = SatelMessageQueue(mock_send_func)
    queued = QueuedMessage(write_msg, True)
    queued.processed_future.set_result(result_msg)
    queue._current_message = queued

    # Should log a warning but not crash
    queue.on_message_received(result_msg)
    assert (
        "Received result but future is already done (likely timed out)" in caplog.text
    )

    assert queued.processed_future.done()
