"""Queue class for Satel Integra"""

import asyncio
from collections.abc import Callable

import logging
from collections.abc import Awaitable

from satel_integra.commands import SatelReadCommand
from satel_integra.const import MESSAGE_RESPONSE_TIMEOUT
from satel_integra.messages import SatelReadMessage, SatelWriteMessage

_LOGGER = logging.getLogger(__name__)


class QueuedMessage:
    def __init__(self, message: SatelWriteMessage, wait_for_result: bool):
        self.message = message
        self.return_result = wait_for_result

        self.processed_future: asyncio.Future[SatelReadMessage] = (
            asyncio.get_running_loop().create_future()
        )

        # Determine the expected response
        self.expected_result_command = (
            message.cmd
            if getattr(message.cmd, "expects_same_cmd_response", False)
            else SatelReadCommand.RESULT
        )


class SatelMessageQueue:
    """Queue ensuring write commands are sent sequentially and wait for a result."""

    def __init__(self, send_func: Callable[[SatelWriteMessage], Awaitable[None]]):
        """
        Args:
            send_func: coroutine function to send a frame, e.g. AsyncSatel._send_data
        """
        self._send_func: Callable[[SatelWriteMessage], Awaitable[None]] = send_func
        self._queue: asyncio.Queue[QueuedMessage] = asyncio.Queue()

        self._current_message: QueuedMessage | None = None
        self._process_task: asyncio.Task | None = None
        self._closed = False

    async def start(self):
        """Start processing the queue."""
        if self._process_task:
            return  # already running
        self._process_task = asyncio.create_task(self._process_queue())

    async def stop(self):
        """Stop the queue gracefully."""
        self._closed = True
        if self._process_task:
            self._process_task.cancel()
            try:
                await self._process_task
            except asyncio.CancelledError:
                pass
            self._process_task = None

    async def add_message(self, msg: SatelWriteMessage, wait_for_result: bool = False):
        """
        Queue a message. If wait_for_result is True, wait for and return the result.
        Otherwise, just queue the message and return None
        """
        if self._closed:
            raise RuntimeError("Queue is stopped")

        _LOGGER.debug("Queueing message: %s", msg)

        queued = QueuedMessage(msg, wait_for_result)
        await self._queue.put(queued)

        if wait_for_result:
            return await queued.processed_future
        return None

    async def _process_queue(self) -> None:
        """Process queued commands sequentially."""
        _LOGGER.debug("Message queue worker started")

        while not self._closed:
            try:
                self._current_message = await self._get_next_message()
                if self._current_message is None:
                    continue

                await self._send_and_wait_response(self._current_message)

            except Exception as e:
                _LOGGER.exception("Unexpected error in queue processing: %s", e)

            finally:
                self._current_message = None

        _LOGGER.debug("Command queue worker stopped")

    async def _get_next_message(self) -> QueuedMessage | None:
        """Get next message from queue with timeout."""
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=0.1)
        except asyncio.TimeoutError:
            return None

    async def _send_and_wait_response(self, queued: QueuedMessage) -> None:
        """Send a queued message and wait for the panel RESULT."""

        try:
            _LOGGER.debug("Sending message: %s", queued.message)
            await self._send_func(queued.message)
        except Exception as exc:
            _LOGGER.exception("Error while sending message: %s", exc)
            if not queued.processed_future.done():
                queued.processed_future.set_exception(exc)

            return

        # Wait for the RESULT (the future will be completed by on_message_received).
        try:
            await asyncio.wait_for(
                queued.processed_future, timeout=MESSAGE_RESPONSE_TIMEOUT
            )
        except asyncio.TimeoutError:
            _LOGGER.error(
                "No response received from panel within %ss", MESSAGE_RESPONSE_TIMEOUT
            )
            return

    def on_message_received(self, result: SatelReadMessage):
        """Called by AsyncSatel when a RESULT message is received."""
        if not self._current_message:
            # Received result but no message is being processed, standard read message due to monitoring
            return

        if self._current_message.processed_future.done():
            _LOGGER.warning(
                "Received result but future is already done (likely timed out)"
            )
            return

        if self._current_message.expected_result_command != result.cmd:
            _LOGGER.warning("Received result but message expects different result")
            return

        self._current_message.processed_future.set_result(result)
