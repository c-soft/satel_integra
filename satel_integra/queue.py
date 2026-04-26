"""Queue class for Satel Integra"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from satel_integra.commands import SatelReadCommand, expected_response_command
from satel_integra.const import MESSAGE_RESPONSE_TIMEOUT
from satel_integra.messages import SatelReadMessage, SatelWriteMessage

_LOGGER = logging.getLogger(__name__)


class QueuedMessage:
    def __init__(self, message: SatelWriteMessage, wait_for_result: bool):
        self.message = message
        self.return_result = wait_for_result

        self.processed_future: asyncio.Future[SatelReadMessage | None] = (
            asyncio.get_running_loop().create_future()
        )

        self.expected_result_command = expected_response_command(message.cmd)


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
        self._stopped = False

    async def start(self):
        """Start processing the queue."""
        if self._process_task:
            return  # already running
        self._process_task = asyncio.create_task(self._process_queue())

    async def stop(self):
        """Stop the queue gracefully."""
        self._stopped = True
        if self._process_task:
            self._process_task.cancel()
            try:
                await self._process_task
            except asyncio.CancelledError:
                pass
            self._process_task = None

        self._cancel_pending_messages()

    async def add_message(self, msg: SatelWriteMessage, wait_for_result: bool = False):
        """
        Queue a message. If wait_for_result is True, wait for and return the result.
        Otherwise, just queue the message and return None
        """
        if self._stopped:
            raise RuntimeError("Queue is stopped")

        _LOGGER.debug("Queueing message: %s", msg)

        queued = QueuedMessage(msg, wait_for_result)
        await self._queue.put(queued)

        if not wait_for_result:
            return

        try:
            return await asyncio.shield(queued.processed_future)
        except asyncio.CancelledError:
            if self._stopped or queued.processed_future.cancelled():
                _LOGGER.debug("Waiting for message result cancelled")
                return
            raise
        except Exception as exc:
            _LOGGER.debug("Couldn't wait for message result: %s", exc)
            return

    def _cancel_pending_messages(self) -> None:
        """Cancel any pending waiters when the queue shuts down."""
        if self._current_message and not self._current_message.processed_future.done():
            self._current_message.processed_future.cancel()

        while not self._queue.empty():
            queued = self._queue.get_nowait()
            if not queued.processed_future.done():
                queued.processed_future.cancel()

    async def _process_queue(self) -> None:
        """Process queued commands sequentially."""
        _LOGGER.debug("Message queue worker started")

        while not self._stopped:
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
        """Send a queued message and wait for its response."""

        try:
            _LOGGER.debug("Sending message: %s", queued.message)
            await self._send_func(queued.message)
        except Exception as exc:
            _LOGGER.debug("Error while sending message: %s", exc)
            if not queued.processed_future.done():
                queued.processed_future.set_exception(exc)

            return

        # Wait for the expected response. The future is completed by on_message_received().
        try:
            await asyncio.wait_for(
                queued.processed_future, timeout=MESSAGE_RESPONSE_TIMEOUT
            )
            _LOGGER.debug("Queued message resolved: %s", queued.message)
        except asyncio.TimeoutError:
            _LOGGER.debug(
                "No response received from panel within %ss for message: %s",
                MESSAGE_RESPONSE_TIMEOUT,
                queued.message,
            )
            if not queued.processed_future.done():
                queued.processed_future.cancel()
            return

    def on_message_received(self, result: SatelReadMessage) -> None:
        """Handle a message if it is relevant to the current queued command."""
        if result.cmd is SatelReadCommand.RESULT or (
            self._current_message is not None
            and self._current_message.expected_result_command == result.cmd
        ):
            self._complete_message(result)

    def _complete_message(self, result: SatelReadMessage) -> None:
        """Complete the current queued command with its response."""
        if not self._current_message:
            # Received a response but no command is being processed, likely monitoring.
            return

        if self._current_message.processed_future.done():
            _LOGGER.debug(
                "Received result but future is already done (likely timed out)"
            )
            return

        if self._current_message.expected_result_command != result.cmd:
            _LOGGER.warning(
                "Received result (%s) for message (%s) but expects different result (%s)",
                result,
                self._current_message.message,
                self._current_message.expected_result_command,
            )
            return

        self._current_message.processed_future.set_result(result)
