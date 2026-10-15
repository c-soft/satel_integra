"""Subscriptions to state changes."""

import inspect
import logging
from collections.abc import Awaitable, Callable

StateChangeCallback = Callable[[], None]
AsyncStateChangeCallback = Callable[[], None | Awaitable[None]]
UnsubscribeCallback = Callable[[], None]

_LOGGER = logging.getLogger(__name__)


class _CallbackRegistry[TCallback]:
    """Store subscriptions and provide independent unsubscribe functions."""

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._callbacks: dict[object, TCallback] = {}

    def add(self, callback: TCallback) -> UnsubscribeCallback:
        """Subscribe without invoking the callback and return its unsubscribe function."""
        key = object()
        self._callbacks[key] = callback

        def unsubscribe() -> None:
            self._callbacks.pop(key, None)

        return unsubscribe


class CallbackRegistry(_CallbackRegistry[StateChangeCallback]):
    """Manage synchronous subscriptions to state changes."""

    def notify(self) -> None:
        """Invoke a snapshot of subscribers, isolating callback failures."""
        for callback in tuple(self._callbacks.values()):
            try:
                callback()
            except Exception:
                _LOGGER.exception("Error in state-change callback")


class AsyncCallbackRegistry(_CallbackRegistry[AsyncStateChangeCallback]):
    """Manage synchronous and asynchronous subscriptions to state changes."""

    async def notify(self) -> None:
        """Invoke a snapshot of subscribers in order, isolating callback failures.

        Subscription changes take effect on the next notification. Awaitable
        results are awaited before invoking the next subscriber, and task
        cancellation propagates to the caller.
        """
        for callback in tuple(self._callbacks.values()):
            try:
                result = callback()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                _LOGGER.exception("Error in state-change callback")
