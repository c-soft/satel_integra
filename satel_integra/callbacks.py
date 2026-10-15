"""Subscriptions to state changes."""

import inspect
import logging

from satel_integra.const import StateChangeCallback, UnsubscribeCallback

_LOGGER = logging.getLogger(__name__)


class CallbackRegistry:
    """Manage subscriptions to state changes."""

    def __init__(self) -> None:
        """Initialize an empty registry."""
        self._callbacks: dict[object, StateChangeCallback] = {}

    def add(self, callback: StateChangeCallback) -> UnsubscribeCallback:
        """Subscribe without invoking the callback and return its unsubscribe function."""
        key = object()
        self._callbacks[key] = callback

        def unsubscribe() -> None:
            self._callbacks.pop(key, None)

        return unsubscribe

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
