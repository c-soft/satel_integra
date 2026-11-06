"""Handlers for command result messages."""

import logging

from satel_integra.messages import SatelTypedReadMessage
from satel_integra.models import SatelCommandResult
from satel_integra.state import SatelState

from .subscriptions import SatelSubscriptions

_LOGGER = logging.getLogger(__name__)


def handle_command_result(
    _state: SatelState,
    _subscriptions: SatelSubscriptions,
    message: SatelTypedReadMessage[SatelCommandResult],
) -> None:
    """Log an unsolicited command result."""
    _LOGGER.debug("Received command result: %s", message.data.code)
