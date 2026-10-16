"""Handlers for system-level panel messages."""

import logging

from satel_integra.messages import SatelTypedReadMessage
from satel_integra.models import SatelRtcAndStatus
from satel_integra.state import SatelState

from .subscriptions import SatelSubscriptions

_LOGGER = logging.getLogger(__name__)


def handle_system_status(
    state: SatelState,
    subscriptions: SatelSubscriptions,
    message: SatelTypedReadMessage[SatelRtcAndStatus],
) -> None:
    """Store changed panel status and notify consumers."""
    status = message.data
    if status == state.system_status:
        return

    state.system_status = status
    _LOGGER.debug("System status changed: %s", status)
    subscriptions.system_status.notify()
