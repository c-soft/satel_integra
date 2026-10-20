"""Dispatch incoming messages to state handlers."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from satel_integra.commands import SatelReadCommand
from satel_integra.messages import (
    SatelReadMessage,
    SatelReadMessageData,
    SatelTypedReadMessage,
)
from satel_integra.state import SatelState

_LOGGER = logging.getLogger(__name__)

MessageHandler = Callable[[SatelReadMessage], None]


@dataclass
class SatelSubscriptions:
    """Subscriptions notified when message-derived state changes."""


class MessageHandlerRegistry:
    """Store and dispatch handlers for incoming messages."""

    def __init__(
        self,
        state: SatelState,
        subscriptions: SatelSubscriptions,
    ) -> None:
        """Initialize the registry with shared state and subscriptions."""
        self._state = state
        self._subscriptions = subscriptions
        self._handlers: dict[SatelReadCommand, MessageHandler] = {}

    def register[TData: SatelReadMessageData](
        self,
        command: SatelReadCommand,
        data_type: type[TData],
        handler: Callable[
            [SatelState, SatelSubscriptions, SatelTypedReadMessage[TData]], None
        ],
    ) -> None:
        """Register a handler that only receives the expected typed message."""

        def typed_handler(message: SatelReadMessage) -> None:
            if (
                not isinstance(message, SatelTypedReadMessage)
                or message.data_type is not data_type
            ):
                _LOGGER.warning(
                    "Received unexpected message type for %s: expected %s",
                    command,
                    data_type.__name__,
                )
                return

            handler(
                self._state,
                self._subscriptions,
                cast(SatelTypedReadMessage[TData], message),
            )

        self._handlers[command] = typed_handler

    def dispatch(self, message: SatelReadMessage) -> bool:
        """Dispatch a message and return whether a handler was registered."""
        handler = self._handlers.get(message.cmd)
        if handler is None:
            return False

        handler(message)
        return True
