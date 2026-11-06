"""Handlers for partition messages."""

import logging

from satel_integra.commands import SatelReadCommand
from satel_integra.messages import SatelTypedReadMessage
from satel_integra.models import SatelBitmaskData
from satel_integra.state import AlarmState, SatelState

from .subscriptions import SatelSubscriptions

_LOGGER = logging.getLogger(__name__)

PARTITION_STATE_BY_COMMAND: dict[SatelReadCommand, AlarmState] = {
    SatelReadCommand.PARTITIONS_ARMED_SUPPRESSED: AlarmState.ARMED_SUPPRESSED,
    SatelReadCommand.PARTITIONS_ARMED_MODE0: AlarmState.ARMED_MODE0,
    SatelReadCommand.PARTITIONS_ARMED_MODE1: AlarmState.ARMED_MODE1,
    SatelReadCommand.PARTITIONS_ARMED_MODE2: AlarmState.ARMED_MODE2,
    SatelReadCommand.PARTITIONS_ARMED_MODE3: AlarmState.ARMED_MODE3,
    SatelReadCommand.PARTITIONS_ENTRY_TIME: AlarmState.ENTRY_TIME,
    SatelReadCommand.PARTITIONS_EXIT_COUNTDOWN_OVER_10: (
        AlarmState.EXIT_COUNTDOWN_OVER_10
    ),
    SatelReadCommand.PARTITIONS_EXIT_COUNTDOWN_UNDER_10: (
        AlarmState.EXIT_COUNTDOWN_UNDER_10
    ),
    SatelReadCommand.PARTITIONS_ALARM: AlarmState.TRIGGERED,
    SatelReadCommand.PARTITIONS_FIRE_ALARM: AlarmState.TRIGGERED_FIRE,
}


def handle_partition_state(
    state: SatelState,
    subscriptions: SatelSubscriptions,
    message: SatelTypedReadMessage[SatelBitmaskData],
) -> None:
    """Handle a partition state message."""
    alarm_state = PARTITION_STATE_BY_COMMAND[message.cmd]
    partitions = sorted(message.data.active)

    state.partition_states[alarm_state] = partitions
    _LOGGER.debug("Update: list of partitions in mode %s: %s", alarm_state, partitions)
    subscriptions.partitions.notify()
