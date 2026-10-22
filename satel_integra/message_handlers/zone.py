"""Handlers for zone messages."""

import logging
from typing import Literal

from satel_integra.commands import SatelReadCommand
from satel_integra.messages import SatelTypedReadMessage
from satel_integra.models import SatelBitmaskData
from satel_integra.state import SatelState

from .subscriptions import SatelSubscriptions

_LOGGER = logging.getLogger(__name__)

type ZoneStateAttribute = Literal["violated", "tampered"]

ZONE_STATE_ATTRIBUTE_BY_COMMAND: dict[SatelReadCommand, ZoneStateAttribute] = {
    SatelReadCommand.ZONES_VIOLATED: "violated",
    SatelReadCommand.ZONES_TAMPER: "tampered",
}


def handle_zone_state(
    state: SatelState,
    subscriptions: SatelSubscriptions,
    message: SatelTypedReadMessage[SatelBitmaskData],
) -> None:
    """Handle a zone state message."""
    state_attribute = ZONE_STATE_ATTRIBUTE_BY_COMMAND[message.cmd]
    represented_zones = len(message.msg_data) * 8
    zone_limit = min(represented_zones, len(state.zones))
    active_zones = {zone for zone in message.data.active if zone <= zone_limit}

    for zone in range(1, zone_limit + 1):
        setattr(state.zones[zone], state_attribute, zone in active_zones)

    _LOGGER.debug("%s zones: %s", state_attribute.capitalize(), active_zones)
    subscriptions.zones.notify()
