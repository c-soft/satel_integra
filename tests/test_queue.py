import logging
from unittest.mock import AsyncMock
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
    queued = QueuedMessage(
        SatelWriteMessage(SatelWriteCommand.READ_DEVICE_NAME), wait_for_result=True
    )
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
