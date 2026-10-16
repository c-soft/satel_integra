import asyncio
import logging
from unittest.mock import AsyncMock, Mock

import pytest

from satel_integra.commands import SatelReadCommand, SatelWriteCommand
from satel_integra.const import CONTROL_COMMAND_TTL
from satel_integra.exceptions import SatelCommandExpiredError, SatelNotConnectedError
from satel_integra.messages import (
    SatelReadMessage,
    SatelTypedReadMessage,
    SatelWriteMessage,
)
from satel_integra.models import SatelCommandResult
from satel_integra.queue import QueuedMessage, SatelMessageQueue


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
    return SatelTypedReadMessage(
        SatelReadCommand.RESULT,
        bytearray([0x01]),
        data_type=SatelCommandResult,
    )


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

    assert mock_queue._stopped is True
    assert task.cancelled()
    assert mock_queue._process_task is None


@pytest.mark.asyncio
async def test_stop_unblocks_waiting_message(mock_queue, write_msg):
    await mock_queue.start()

    waiter = asyncio.create_task(mock_queue.add_message(write_msg, True))
    await asyncio.sleep(0)

    await mock_queue.stop()

    result = await asyncio.wait_for(waiter, timeout=1.0)
    assert result is None


@pytest.mark.asyncio
async def test_queued_message_init(write_msg):
    message = QueuedMessage(write_msg, False)

    assert message.return_result is False
    assert message.expected_result_command is SatelReadCommand.RESULT


@pytest.mark.asyncio
async def test_queued_message_init_same_cmd():
    write_msg = SatelWriteMessage(SatelReadCommand.READ_DEVICE_NAME)
    message = QueuedMessage(write_msg, True)

    assert message.return_result is True
    assert message.expected_result_command is SatelReadCommand.READ_DEVICE_NAME


@pytest.mark.asyncio
async def test_queued_message_init_rtc_and_status_expects_same_cmd():
    write_msg = SatelWriteMessage(SatelReadCommand.RTC_AND_STATUS)
    message = QueuedMessage(write_msg, True)

    assert message.expected_result_command is SatelReadCommand.RTC_AND_STATUS


@pytest.mark.asyncio
async def test_deprecated_write_query_command_expects_matching_read_response():
    with pytest.warns(DeprecationWarning, match="SatelReadCommand.RTC_AND_STATUS"):
        write_msg = SatelWriteMessage(SatelWriteCommand.RTC_AND_STATUS)

    message = QueuedMessage(write_msg, True)

    assert message.expected_result_command is SatelReadCommand.RTC_AND_STATUS


@pytest.mark.asyncio
async def test_queued_message_rejects_result_as_outbound_command():
    with pytest.raises(ValueError, match="RESULT cannot be sent"):
        SatelWriteMessage(SatelReadCommand.RESULT)


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
    assert mock_queue._stopped is True


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
async def test_on_message_received_completes_result_for_read_query(
    mock_queue, result_msg, caplog
):
    caplog.at_level(logging.WARNING)

    queued = QueuedMessage(SatelWriteMessage(SatelReadCommand.READ_DEVICE_NAME), True)
    mock_queue._current_message = queued
    mock_queue.on_message_received(result_msg)

    assert "expects different result" in caplog.text
    assert queued.processed_future.done()
    assert queued.processed_future.result() is result_msg


@pytest.mark.asyncio
async def test_on_message_received_completes_current_expected_command(mock_queue):
    queued = QueuedMessage(SatelWriteMessage(SatelReadCommand.RTC_AND_STATUS), True)
    mock_queue._current_message = queued
    msg = SatelReadMessage(SatelReadCommand.RTC_AND_STATUS, bytearray())

    mock_queue.on_message_received(msg)

    assert queued.processed_future.done()


@pytest.mark.asyncio
async def test_on_message_received_logs_unrelated_read_command(mock_queue, caplog):
    caplog.at_level(logging.WARNING)
    queued = QueuedMessage(SatelWriteMessage(SatelReadCommand.RTC_AND_STATUS), True)
    mock_queue._current_message = queued
    msg = SatelReadMessage(SatelReadCommand.ZONES_VIOLATED, bytearray())

    mock_queue.on_message_received(msg)

    assert "expects different result" not in caplog.text
    assert not queued.processed_future.done()


@pytest.mark.asyncio
async def test_complete_message_completes_current_expected_command(mock_queue):
    queued = QueuedMessage(SatelWriteMessage(SatelReadCommand.RTC_AND_STATUS), True)
    mock_queue._current_message = queued
    msg = SatelReadMessage(SatelReadCommand.RTC_AND_STATUS, bytearray())

    mock_queue._complete_message(msg)

    assert queued.processed_future.done()


@pytest.mark.asyncio
async def test_complete_message_logs_unrelated_read_command(mock_queue, caplog):
    caplog.at_level(logging.WARNING)
    queued = QueuedMessage(SatelWriteMessage(SatelReadCommand.RTC_AND_STATUS), True)
    mock_queue._current_message = queued
    msg = SatelReadMessage(SatelReadCommand.ZONES_VIOLATED, bytearray())

    mock_queue._complete_message(msg)

    assert "expects different result" in caplog.text
    assert not queued.processed_future.done()


@pytest.mark.asyncio
async def test_on_message_received_no_current_message(mock_queue, result_msg):
    # No current message set — should simply return
    result = mock_queue.on_message_received(result_msg)
    assert result is None


@pytest.mark.asyncio
async def test_on_message_received_logs_result_without_current_message(
    mock_queue, result_msg, caplog
):
    with caplog.at_level(logging.DEBUG):
        mock_queue.on_message_received(result_msg)

    assert "Received RESULT with no pending queued message" in caplog.text


@pytest.mark.asyncio
async def test_on_message_received_future_already_done(
    mock_queue, write_msg, result_msg, caplog
):
    queued = QueuedMessage(write_msg, True)
    queued.processed_future.set_result(result_msg)
    mock_queue._current_message = queued

    # Should log a warning but not crash
    with caplog.at_level(logging.DEBUG):
        mock_queue.on_message_received(result_msg)

    assert (
        "Received result but future is already done (likely timed out)" in caplog.text
    )

    assert queued.processed_future.done()


@pytest.mark.asyncio
async def test_process_queue(mock_queue, write_msg):
    queued = QueuedMessage(write_msg, True)

    def close_queue_and_return():
        mock_queue._stopped = True
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
        mock_queue._stopped = True
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
        mock_queue._stopped = True

    mock_queue._send_and_wait_response = AsyncMock()
    mock_queue._get_next_message = AsyncMock(side_effect=close_queue, return_value=None)

    await mock_queue._process_queue()
    mock_queue._send_and_wait_response.assert_not_awaited()

    assert mock_queue._current_message is None


@pytest.mark.asyncio
async def test_send_and_wait_response_success(
    mock_queue, write_msg, result_msg, caplog
):
    mock_queue._send_func = AsyncMock()

    queued = QueuedMessage(write_msg, False)
    queued.processed_future.set_result(result_msg)

    with caplog.at_level(logging.DEBUG):
        await mock_queue._send_and_wait_response(queued)

    mock_queue._send_func.assert_awaited_once_with(write_msg)

    assert "Queued message resolved:" in caplog.text
    assert queued.processed_future.done()


@pytest.mark.asyncio
async def test_send_and_wait_response_send_func_exception(
    mock_queue, write_msg, caplog
):
    mock_queue._send_func = AsyncMock(side_effect=ConnectionError("Test exception"))

    queued = QueuedMessage(write_msg, False)

    with caplog.at_level(logging.DEBUG):
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

    with caplog.at_level(logging.DEBUG):
        await mock_queue._send_and_wait_response(queued)

    assert "No response received from panel within" in caplog.text
    assert "for message:" in caplog.text
    assert queued.processed_future.done()
    assert queued.processed_future.cancelled()


def _outputs_msg(cmd, outputs, code="1234"):
    return SatelWriteMessage(cmd, code=code, zones_or_outputs=outputs)


@pytest.mark.asyncio
async def test_add_message_groups_pending_outputs(mock_queue):
    await mock_queue.add_message(_outputs_msg(SatelWriteCommand.OUTPUTS_OFF, [1]))
    await mock_queue.add_message(_outputs_msg(SatelWriteCommand.OUTPUTS_OFF, [2]))
    await mock_queue.add_message(_outputs_msg(SatelWriteCommand.OUTPUTS_OFF, [10]))

    pending = mock_queue._queue.pending()
    assert len(pending) == 1
    assert (
        pending[0].message.msg_data
        == _outputs_msg(SatelWriteCommand.OUTPUTS_OFF, [1, 2, 10]).msg_data
    )


@pytest.mark.asyncio
async def test_add_message_groups_pending_partitions(mock_queue):
    await mock_queue.add_message(
        SatelWriteMessage(
            SatelWriteCommand.PARTITIONS_DISARM, code="1234", partitions=[1]
        )
    )
    await mock_queue.add_message(
        SatelWriteMessage(
            SatelWriteCommand.PARTITIONS_DISARM, code="1234", partitions=[3]
        )
    )

    pending = mock_queue._queue.pending()
    assert len(pending) == 1
    assert (
        pending[0].message.msg_data
        == SatelWriteMessage(
            SatelWriteCommand.PARTITIONS_DISARM, code="1234", partitions=[1, 3]
        ).msg_data
    )


@pytest.mark.asyncio
async def test_add_message_does_not_group_different_command_or_code(mock_queue):
    await mock_queue.add_message(_outputs_msg(SatelWriteCommand.OUTPUTS_ON, [1]))
    await mock_queue.add_message(_outputs_msg(SatelWriteCommand.OUTPUTS_OFF, [2]))
    await mock_queue.add_message(
        _outputs_msg(SatelWriteCommand.OUTPUTS_OFF, [3], code="9999")
    )

    assert len(mock_queue._queue.pending()) == 3


@pytest.mark.asyncio
async def test_add_message_does_not_group_raw_messages(mock_queue, write_msg):
    await mock_queue.add_message(write_msg)
    await mock_queue.add_message(
        SatelWriteMessage(SatelWriteCommand.PARTITIONS_DISARM, raw_data=bytearray([1]))
    )

    assert len(mock_queue._queue.pending()) == 2


@pytest.mark.asyncio
async def test_add_message_groups_into_waiting_message(mock_queue):
    queued = QueuedMessage(_outputs_msg(SatelWriteCommand.OUTPUTS_ON, [1]), True)
    await mock_queue._queue.put(queued)

    await mock_queue.add_message(_outputs_msg(SatelWriteCommand.OUTPUTS_ON, [2]))

    pending = mock_queue._queue.pending()
    assert pending == [queued]
    assert (
        queued.message.msg_data
        == _outputs_msg(SatelWriteCommand.OUTPUTS_ON, [1, 2]).msg_data
    )


@pytest.mark.asyncio
async def test_waiting_message_groups_into_non_waiting_message(mock_queue, result_msg):
    queued = QueuedMessage(_outputs_msg(SatelWriteCommand.OUTPUTS_ON, [1]), False)
    await mock_queue._queue.put(queued)

    waiter = asyncio.create_task(
        mock_queue.add_message(
            _outputs_msg(SatelWriteCommand.OUTPUTS_ON, [2]),
            wait_for_result=True,
        )
    )
    await asyncio.sleep(0)

    assert mock_queue._queue.pending() == [queued]
    assert queued.return_result is True
    assert not waiter.done()

    queued.processed_future.set_result(result_msg)
    assert await waiter is result_msg


@pytest.mark.asyncio
async def test_add_message_groups_past_unrelated_messages(mock_queue):
    await mock_queue.add_message(_outputs_msg(SatelWriteCommand.OUTPUTS_ON, [1]))
    await mock_queue.add_message(_outputs_msg(SatelWriteCommand.OUTPUTS_OFF, [2]))
    await mock_queue.add_message(_outputs_msg(SatelWriteCommand.OUTPUTS_ON, [3]))

    pending = mock_queue._queue.pending()
    assert len(pending) == 2
    assert (
        pending[0].message.msg_data
        == _outputs_msg(SatelWriteCommand.OUTPUTS_ON, [1, 3]).msg_data
    )


@pytest.mark.asyncio
async def test_add_message_keeps_order_for_same_device(mock_queue):
    await mock_queue.add_message(_outputs_msg(SatelWriteCommand.OUTPUTS_ON, [1]))
    await mock_queue.add_message(_outputs_msg(SatelWriteCommand.OUTPUTS_OFF, [1]))
    await mock_queue.add_message(_outputs_msg(SatelWriteCommand.OUTPUTS_ON, [1]))

    pending = mock_queue._queue.pending()
    assert [queued.message.cmd for queued in pending] == [
        SatelWriteCommand.OUTPUTS_ON,
        SatelWriteCommand.OUTPUTS_OFF,
        SatelWriteCommand.OUTPUTS_ON,
    ]


@pytest.mark.asyncio
async def test_concurrent_outputs_are_sent_in_single_frame(mock_queue, result_msg):
    """Outputs switched together (e.g. a group in Home Assistant) use one frame."""

    async def send(msg):
        asyncio.get_running_loop().call_soon(mock_queue.on_message_received, result_msg)

    mock_queue._send_func = AsyncMock(side_effect=send)
    await mock_queue.start()

    async def switch_output(output):
        await asyncio.sleep(0.01)
        await mock_queue.add_message(
            _outputs_msg(SatelWriteCommand.OUTPUTS_OFF, [output])
        )

    await asyncio.gather(*(switch_output(output) for output in (1, 5, 9, 17)))
    await asyncio.sleep(0.2)
    await mock_queue.stop()

    mock_queue._send_func.assert_awaited_once()
    sent = mock_queue._send_func.await_args.args[0]
    assert (
        sent.msg_data
        == _outputs_msg(SatelWriteCommand.OUTPUTS_OFF, [1, 5, 9, 17]).msg_data
    )


@pytest.mark.asyncio
async def test_concurrent_waiting_outputs_share_one_result(mock_queue, result_msg):
    """Grouped callers wait for the response to the single combined frame."""

    async def send(_msg):
        asyncio.get_running_loop().call_soon(mock_queue.on_message_received, result_msg)

    mock_queue._send_func = AsyncMock(side_effect=send)
    await mock_queue.start()

    results = await asyncio.gather(
        mock_queue.add_message(
            _outputs_msg(SatelWriteCommand.OUTPUTS_ON, [1]), wait_for_result=True
        ),
        mock_queue.add_message(
            _outputs_msg(SatelWriteCommand.OUTPUTS_ON, [2]), wait_for_result=True
        ),
        mock_queue.add_message(
            _outputs_msg(SatelWriteCommand.OUTPUTS_ON, [3]), wait_for_result=True
        ),
    )
    await mock_queue.stop()

    assert all(result is result_msg for result in results)
    mock_queue._send_func.assert_awaited_once()
    sent = mock_queue._send_func.await_args.args[0]
    assert (
        sent.msg_data == _outputs_msg(SatelWriteCommand.OUTPUTS_ON, [1, 2, 3]).msg_data
    )


@pytest.mark.asyncio
async def test_result_during_grouping_does_not_complete_waiters(mock_queue, result_msg):
    """A result received before the grouped frame is sent must be ignored."""
    sent_result = SatelTypedReadMessage(
        SatelReadCommand.RESULT,
        bytearray([0x01]),
        data_type=SatelCommandResult,
    )

    async def send(_msg):
        asyncio.get_running_loop().call_soon(
            mock_queue.on_message_received, sent_result
        )

    mock_queue._send_func = AsyncMock(side_effect=send)
    await mock_queue.start()

    waiters = [
        asyncio.create_task(
            mock_queue.add_message(
                _outputs_msg(SatelWriteCommand.OUTPUTS_ON, [output]),
                wait_for_result=True,
            )
        )
        for output in (1, 2, 3)
    ]
    grouped_data = _outputs_msg(SatelWriteCommand.OUTPUTS_ON, [1, 2, 3]).msg_data
    while (
        not mock_queue._is_grouping
        or mock_queue._current_message is None
        or mock_queue._current_message.message.msg_data != grouped_data
    ):
        await asyncio.sleep(0)

    mock_queue.on_message_received(result_msg)

    assert mock_queue._current_message is not None
    assert not mock_queue._current_message.processed_future.done()
    assert all(not waiter.done() for waiter in waiters)
    mock_queue._send_func.assert_not_awaited()

    results = await asyncio.gather(*waiters)
    await mock_queue.stop()

    mock_queue._send_func.assert_awaited_once()
    assert all(result is sent_result for result in results)


@pytest.mark.asyncio
async def test_concurrent_waiting_non_mergeable_messages_are_sent_separately(
    mock_queue,
):
    """Waiting for results does not make additional command types groupable."""

    async def send(_msg):
        result = SatelReadMessage(SatelReadCommand.RTC_AND_STATUS, bytearray())
        asyncio.get_running_loop().call_soon(mock_queue.on_message_received, result)

    mock_queue._send_func = AsyncMock(side_effect=send)
    await mock_queue.start()

    await asyncio.gather(
        mock_queue.add_message(
            SatelWriteMessage(SatelReadCommand.RTC_AND_STATUS),
            wait_for_result=True,
        ),
        mock_queue.add_message(
            SatelWriteMessage(SatelReadCommand.RTC_AND_STATUS),
            wait_for_result=True,
        ),
    )
    await mock_queue.stop()

    assert mock_queue._send_func.await_count == 2


@pytest.mark.asyncio
async def test_wait_for_grouping_skips_non_mergeable(
    mock_queue, write_msg, monkeypatch
):
    mock_sleep = AsyncMock()
    monkeypatch.setattr("satel_integra.queue.asyncio.sleep", mock_sleep)

    await mock_queue._wait_for_grouping(QueuedMessage(write_msg, False))

    mock_sleep.assert_not_awaited()
    assert mock_queue._is_grouping is False


@pytest.fixture
def link():
    """Connection state seen by a queue created with connected/generation."""
    return {"connected": True, "generation": 1}


@pytest.fixture
def bound_queue(link):
    return SatelMessageQueue(
        AsyncMock(),
        connected=lambda: link["connected"],
        generation=lambda: link["generation"],
    )


def _output_on():
    return SatelWriteMessage(
        SatelWriteCommand.OUTPUTS_ON, code="1234", zones_or_outputs=[3]
    )


@pytest.mark.asyncio
async def test_control_command_without_connection_fails_immediately(bound_queue, link):
    link["connected"] = False

    with pytest.raises(SatelNotConnectedError):
        await bound_queue.add_message(_output_on())

    assert bound_queue._queue.empty()


@pytest.mark.asyncio
async def test_query_without_connection_is_still_queued(bound_queue, link):
    link["connected"] = False

    await bound_queue.add_message(SatelWriteMessage(SatelReadCommand.RTC_AND_STATUS))

    assert bound_queue._queue.qsize() == 1


@pytest.mark.asyncio
async def test_control_command_from_previous_connection_is_not_sent(bound_queue, link):
    queued = QueuedMessage(_output_on(), True, generation=1)
    link["generation"] = 2  # reconnected before the command was sent

    await bound_queue._send_and_wait_response(queued)

    bound_queue._send_func.assert_not_awaited()
    with pytest.raises(SatelCommandExpiredError):
        queued.processed_future.result()


@pytest.mark.asyncio
async def test_dropped_fire_and_forget_command_resolves_to_none(bound_queue, link):
    queued = QueuedMessage(_output_on(), False, generation=1)
    link["generation"] = 2

    await bound_queue._send_and_wait_response(queued)

    bound_queue._send_func.assert_not_awaited()
    assert queued.processed_future.result() is None


@pytest.mark.asyncio
async def test_waiting_caller_learns_command_was_dropped(bound_queue, monkeypatch):
    monkeypatch.setattr("satel_integra.queue.CONTROL_COMMAND_TTL", -1)
    await bound_queue.start()
    try:
        with pytest.raises(SatelCommandExpiredError):
            await bound_queue.add_message(_output_on(), True)
    finally:
        await bound_queue.stop()

    bound_queue._send_func.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_control_command_is_not_sent(bound_queue):
    queued = QueuedMessage(_output_on(), False, generation=1)
    queued.created -= CONTROL_COMMAND_TTL + 1

    await bound_queue._send_and_wait_response(queued)

    bound_queue._send_func.assert_not_awaited()


@pytest.mark.asyncio
async def test_fresh_control_command_is_sent(bound_queue, result_msg):
    queued = QueuedMessage(_output_on(), True, generation=1)
    queued.processed_future.set_result(result_msg)

    await bound_queue._send_and_wait_response(queued)

    bound_queue._send_func.assert_awaited_once_with(queued.message)


@pytest.mark.asyncio
async def test_message_not_grouped_into_one_from_previous_connection(bound_queue, link):
    off = SatelWriteCommand.OUTPUTS_OFF
    await bound_queue.add_message(
        SatelWriteMessage(off, code="1234", zones_or_outputs=[1])
    )
    link["generation"] = 2  # reconnected; the pending command above is now stale
    await bound_queue.add_message(
        SatelWriteMessage(off, code="1234", zones_or_outputs=[2])
    )

    pending = bound_queue._queue.pending()
    assert len(pending) == 2
    assert [q.generation for q in pending] == [1, 2]
