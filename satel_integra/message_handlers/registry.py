"""Dispatch incoming messages to typed handlers."""

import logging
from collections.abc import Callable
from typing import cast

from satel_integra.commands import SatelReadCommand
from satel_integra.messages import (
    SatelReadMessage,
    SatelReadMessageData,
    SatelTypedReadMessage,
)
from satel_integra.models import (
    SatelBitmaskData,
    SatelCommandResult,
    SatelRtcAndStatus,
    SatelSystemTroubles,
)
from satel_integra.state import SatelState

from .output import handle_output_states
from .partition import PARTITION_STATE_BY_COMMAND, handle_partition_state
from .result import handle_command_result
from .subscriptions import SatelSubscriptions
from .system import handle_system_status, handle_system_troubles
from .zone import handle_zone_violations

_LOGGER = logging.getLogger(__name__)

MessageHandler = Callable[[SatelReadMessage], None]


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
        self._register_handlers()

    def _register_handlers(self) -> None:
        """Register the built-in message handlers."""
        for command in PARTITION_STATE_BY_COMMAND:
            self.register(
                command,
                SatelBitmaskData,
                handle_partition_state,
            )

        self.register(
            SatelReadCommand.ZONES_VIOLATED,
            SatelBitmaskData,
            handle_zone_violations,
        )
        self.register(
            SatelReadCommand.OUTPUTS_STATE,
            SatelBitmaskData,
            handle_output_states,
        )
        self.register(
            SatelReadCommand.RESULT,
            SatelCommandResult,
            handle_command_result,
        )
        self.register(
            SatelReadCommand.RTC_AND_STATUS,
            SatelRtcAndStatus,
            handle_system_status,
        )
        self.register(
            SatelReadCommand.TROUBLES_PART_1,
            SatelSystemTroubles,
            handle_system_troubles,
        )

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
