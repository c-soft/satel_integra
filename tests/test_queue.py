import asyncio
import logging
from unittest.mock import AsyncMock, Mock
import pytest

from satel_integra.queue import SatelMessageQueue, QueuedMessage
from satel_integra.messages import SatelReadMessage, SatelWriteMessage
from satel_integra.commands import SatelReadCommand, SatelWriteCommand


@pytest.fixture
def mock_queue():
    """Mock async send function."""
    return SatelMessageQueue(AsyncMock())


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
async def test_start_creates_task(mock_queue):
    mock_queue = mock_queue
    mock_queue._process_queue = AsyncMock()

    await mock_queue.start()

    assert mock_queue._process_task is not None
    mock_queue._process_queue.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_already_running(mock_queue, monkeypatch):
    mock_queue._process_queue = AsyncMock()

    existing_task = Mock()
    mock_queue._process_task = existing_task

    mock_create_task = Mock()
    monkeypatch.setattr("satel_integra.queue.asyncio.create_task", mock_create_task)

    await mock_queue.start()

    mock_create_task.assert_not_called()
    assert mock_queue._process_task is existing_task


@pytest.mark.asyncio
async def test_stop(mock_queue):
    # Create a dummy task that will never complete
    async def dummy_coro():
        await asyncio.sleep(999)

    task = asyncio.create_task(dummy_coro())

    mock_queue._process_task = task

    await mock_queue.stop()

    assert mock_queue._closed is True
    assert task.cancelled()
    assert mock_queue._process_task is None


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
async def test_get_next_message(mock_queue, write_msg):
    queued = QueuedMessage(write_msg, False)
    await mock_queue._queue.put(queued)

    result = await mock_queue._get_next_message()

    assert result is not None
    assert result is queued


@pytest.mark.asyncio
async def test_get_next_message_empty_queue(mock_queue):
    result = await mock_queue._get_next_message()

    assert result is None


@pytest.mark.asyncio
async def test_stop_cancels_task(mock_queue):
    await mock_queue.start()
    await mock_queue.stop()
    assert mock_queue._process_task is None
    assert mock_queue._closed is True


@pytest.mark.asyncio
async def test_add_message_wait_for_result(mock_queue, write_msg, result_msg):
    await mock_queue.start()

    async def complete_result():
        await asyncio.sleep(0.01)
        mock_queue.on_message_received(result_msg)

    asyncio.create_task(complete_result())

    result = await mock_queue.add_message(write_msg, True)

    assert result is result_msg

    mock_queue._send_func.assert_awaited_once_with(write_msg)


@pytest.mark.asyncio
async def test_add_message_no_wait(mock_queue, write_msg):
    await mock_queue.start()
    result = await mock_queue.add_message(write_msg, False)
    await asyncio.sleep(0.05)
    await mock_queue.stop()

    mock_queue._send_func.assert_awaited_once_with(write_msg)

    assert result is None


@pytest.mark.asyncio
async def test_add_message_after_stop_raises(mock_queue, write_msg):
    await mock_queue.start()
    await mock_queue.stop()

    with pytest.raises(RuntimeError, match="Queue is stopped"):
        await mock_queue.add_message(write_msg)


@pytest.mark.asyncio
async def test_on_message_received_correct(mock_queue, write_msg, result_msg):
    queued = QueuedMessage(write_msg, True)
    mock_queue._current_message = queued

    mock_queue.on_message_received(result_msg)

    assert queued.processed_future.done()


@pytest.mark.asyncio
async def test_on_message_received_commmand_mismatch(mock_queue, result_msg, caplog):
    caplog.at_level(logging.WARNING)

    queued = QueuedMessage(SatelWriteMessage(SatelWriteCommand.READ_DEVICE_NAME), True)
    mock_queue._current_message = queued
    mock_queue.on_message_received(result_msg)

    assert "Received result but message expects different result" in caplog.text

    assert not queued.processed_future.done()


@pytest.mark.asyncio
async def test_on_message_received_no_current_message(mock_queue, result_msg):
    # No current message set — should simply return
    result = mock_queue.on_message_received(result_msg)
    assert result is None


@pytest.mark.asyncio
async def test_on_message_received_future_already_done(
    mock_queue, write_msg, result_msg, caplog
):
    caplog.at_level(logging.WARNING)

    queued = QueuedMessage(write_msg, True)
    queued.processed_future.set_result(result_msg)
    mock_queue._current_message = queued

    # Should log a warning but not crash
    mock_queue.on_message_received(result_msg)
    assert (
        "Received result but future is already done (likely timed out)" in caplog.text
    )

    assert queued.processed_future.done()


@pytest.mark.asyncio
async def test_process_queue(mock_queue, write_msg):
    queued = QueuedMessage(write_msg, True)

    def close_queue_and_return():
        mock_queue._closed = True
        return queued

    mock_queue._send_and_wait_response = AsyncMock()
    mock_queue._get_next_message = AsyncMock(side_effect=close_queue_and_return)

    await mock_queue._process_queue()

    mock_queue._send_and_wait_response.assert_awaited_once_with(queued)

    assert mock_queue._current_message is None


@pytest.mark.asyncio
async def test_process_queue_with_exception(mock_queue, write_msg, caplog):
    caplog.at_level(logging.WARNING)

    queued = QueuedMessage(write_msg, True)

    def close_queue_and_return(msg):
        mock_queue._closed = True
        raise Exception("Test exception")

    mock_queue._send_and_wait_response = AsyncMock(side_effect=close_queue_and_return)
    mock_queue._get_next_message = AsyncMock(return_value=queued)

    await mock_queue._process_queue()

    mock_queue._send_and_wait_response.assert_awaited_once_with(queued)
    assert mock_queue._current_message is None
    assert "Unexpected error in queue processing: Test exception" in caplog.text


@pytest.mark.asyncio
async def test_process_queue_skips_none(mock_queue):
    def close_queue():
        mock_queue._closed = True

    mock_queue._send_and_wait_response = AsyncMock()
    mock_queue._get_next_message = AsyncMock(side_effect=close_queue, return_value=None)

    await mock_queue._process_queue()
    mock_queue._send_and_wait_response.assert_not_awaited()

    assert mock_queue._current_message is None


@pytest.mark.asyncio
async def test_send_and_wait_response_success(mock_queue, write_msg, result_msg):
    mock_queue._send_func = AsyncMock()

    queued = QueuedMessage(write_msg, False)
    queued.processed_future.set_result(result_msg)

    await mock_queue._send_and_wait_response(queued)

    mock_queue._send_func.assert_awaited_once_with(write_msg)

    assert queued.processed_future.done()


@pytest.mark.asyncio
async def test_send_and_wait_response_send_func_exception(
    mock_queue, write_msg, caplog
):
    mock_queue._send_func = AsyncMock(side_effect=ConnectionError("Test exception"))

    queued = QueuedMessage(write_msg, False)

    with caplog.at_level("ERROR"):
        await mock_queue._send_and_wait_response(queued)

    assert "Error while sending message: Test exception" in caplog.text
    assert queued.processed_future.done()
    exc = queued.processed_future.exception()
    assert isinstance(exc, ConnectionError)
    assert str(exc) == "Test exception"


@pytest.mark.asyncio
async def test_send_and_wait_response_timeout(
    mock_queue, write_msg, caplog, monkeypatch
):
    mock_queue._send_func = AsyncMock()

    queued = QueuedMessage(write_msg, False)

    # Use a very short timeout for faster test
    monkeypatch.setattr("satel_integra.queue.MESSAGE_RESPONSE_TIMEOUT", 0.01)

    with caplog.at_level("ERROR"):
        await mock_queue._send_and_wait_response(queued)

    assert "No response received from panel within" in caplog.text
    assert queued.processed_future.done()
    assert queued.processed_future.cancelled()
