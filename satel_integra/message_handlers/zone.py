"""Handlers for zone messages."""

import logging

from satel_integra.messages import SatelTypedReadMessage
from satel_integra.models import SatelBitmaskData
from satel_integra.state import SatelState

from .subscriptions import SatelSubscriptions

_LOGGER = logging.getLogger(__name__)


def handle_zone_violations(
    state: SatelState,
    subscriptions: SatelSubscriptions,
    message: SatelTypedReadMessage[SatelBitmaskData],
) -> None:
    """Handle a zone violation message."""
    represented_zones = len(message.msg_data) * 8
    zone_limit = min(represented_zones, len(state.zones))
    violated_zones = {zone for zone in message.data.active if zone <= zone_limit}

    for zone in range(1, zone_limit + 1):
        state.zones[zone].violated = zone in violated_zones

    _LOGGER.debug("Violated zones: %s", violated_zones)
    subscriptions.zones.notify()


def handle_zone_tamper(
    state: SatelState,
    subscriptions: SatelSubscriptions,
    message: SatelTypedReadMessage[SatelBitmaskData],
) -> None:
    """Handle a zone tamper message."""
    represented_zones = len(message.msg_data) * 8
    zone_limit = min(represented_zones, len(state.zones))
    tampered_zones = {zone for zone in message.data.active if zone <= zone_limit}

    for zone in range(1, zone_limit + 1):
        state.zones[zone].tampered = zone in tampered_zones

    _LOGGER.debug("Tampered zones: %s", tampered_zones)
    subscriptions.zones.notify()
