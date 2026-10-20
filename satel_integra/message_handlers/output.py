"""Handlers for output messages."""

import logging

from satel_integra.messages import SatelTypedReadMessage
from satel_integra.models import SatelBitmaskData
from satel_integra.state import SatelState

from .subscriptions import SatelSubscriptions

_LOGGER = logging.getLogger(__name__)


def handle_output_states(
    state: SatelState,
    subscriptions: SatelSubscriptions,
    message: SatelTypedReadMessage[SatelBitmaskData],
) -> None:
    """Handle an output state message."""
    represented_outputs = len(message.msg_data) * 8
    output_limit = min(represented_outputs, len(state.outputs))
    active_outputs = {
        output for output in message.data.active if output <= output_limit
    }

    for output in range(1, output_limit + 1):
        state.outputs[output].active = output in active_outputs

    _LOGGER.debug("Output states: %s", active_outputs)
    subscriptions.outputs.notify()
