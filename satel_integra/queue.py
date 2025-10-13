import asyncio
from collections.abc import Callable
import logging
from collections.abc import Awaitable

from satel_integra.messages import SatelReadMessage, SatelWriteMessage

_LOGGER = logging.getLogger(__name__)


class SatelMessageQueue:
    """Queue ensuring write commands are sent sequentially and wait for a result."""

    def __init__(self, send_func: Callable[[SatelWriteMessage], Awaitable[None]]):
        """
        Args:
            send_func: coroutine function to send a frame, e.g. AsyncSatel._send_data
        """
        self._send_func: Callable[[SatelWriteMessage], Awaitable[None]] = send_func
        self._queue: asyncio.Queue[SatelWriteMessage] = asyncio.Queue()
        self._result_event = asyncio.Event()
        self._last_sent: SatelWriteMessage | None = None

        self._last_result = None
        self._task: asyncio.Task | None = None
        self._closed = False

    async def start(self):
        """Start processing the queue."""
        if self._task:
            return  # already running
        self._task = asyncio.create_task(self._process_queue())

    async def stop(self):
        """Stop the queue gracefully."""
        self._closed = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def add_message(self, msg: SatelWriteMessage):
        """Add a message to the queue"""
        if self._closed:
            raise RuntimeError("Queue is stopped")

        await self._queue.put(msg)

    async def _process_queue(self) -> None:
        """Process queued commands sequentially."""
        _LOGGER.debug("Message queue worker started")

        while not self._closed:
            try:
                # Check with timeout to properly stop when _closed is True instead of blocking
                msg = await asyncio.wait_for(self._queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

            try:
                self._result_event.clear()
                _LOGGER.debug("Sending message: %s", msg)
                await self._send_func(msg)

                # Wait for RESULT message or timeout
                try:
                    await asyncio.wait_for(self._result_event.wait(), timeout=5)
                except asyncio.TimeoutError:
                    _LOGGER.warning("Timeout waiting for result for %s", msg.cmd)
                finally:
                    self._queue.task_done()

            except Exception as exc:
                _LOGGER.exception("Error sending message %s: %s", msg, exc)
                self._queue.task_done()

        _LOGGER.debug("Command queue worker stopped")

    def on_result_message(self, result_msg: SatelReadMessage):
        """Called by AsyncSatel when a RESULT message is received."""
        self._last_result = result_msg
        self._result_event.set()
