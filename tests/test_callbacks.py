import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from satel_integra.callbacks import CallbackRegistry


@pytest.mark.asyncio
async def test_notify_awaits_callbacks_in_registration_order():
    registry = CallbackRegistry()
    calls = []

    async def async_callback():
        await asyncio.sleep(0)
        calls.append("async")

    registry.add(lambda: calls.append("first"))
    registry.add(async_callback)
    registry.add(lambda: calls.append("last"))
    assert calls == []

    await registry.notify()

    assert calls == ["first", "async", "last"]


@pytest.mark.asyncio
async def test_unsubscribe_is_idempotent_and_independent_for_duplicate_callbacks():
    registry = CallbackRegistry()
    callback = MagicMock()
    unsubscribe_first = registry.add(callback)
    unsubscribe_second = registry.add(callback)

    unsubscribe_second()
    unsubscribe_second()
    await registry.notify()

    callback.assert_called_once_with()

    unsubscribe_first()
    await registry.notify()

    callback.assert_called_once_with()


@pytest.mark.asyncio
async def test_subscription_changes_apply_to_next_notification():
    registry = CallbackRegistry()
    calls = []

    def first_callback():
        calls.append("first")
        unsubscribe_first()
        unsubscribe_second()
        registry.add(lambda: calls.append("new"))

    unsubscribe_first = registry.add(first_callback)
    unsubscribe_second = registry.add(lambda: calls.append("second"))

    await registry.notify()
    assert calls == ["first", "second"]

    await registry.notify()
    assert calls == ["first", "second", "new"]


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_type", [MagicMock, AsyncMock])
async def test_callback_failure_is_logged_and_other_subscribers_are_notified(
    callback_type, caplog
):
    registry = CallbackRegistry()
    registry.add(callback_type(side_effect=ValueError("callback failed")))
    next_callback = MagicMock()
    registry.add(next_callback)

    with caplog.at_level(logging.ERROR):
        await registry.notify()

    next_callback.assert_called_once_with()
    assert "Error in state-change callback" in caplog.text
    assert "callback failed" in caplog.text


@pytest.mark.asyncio
async def test_cancellation_propagates():
    registry = CallbackRegistry()
    registry.add(AsyncMock(side_effect=asyncio.CancelledError))
    next_callback = MagicMock()
    registry.add(next_callback)

    with pytest.raises(asyncio.CancelledError):
        await registry.notify()

    next_callback.assert_not_called()
