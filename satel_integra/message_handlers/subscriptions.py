"""Subscriptions notified when message-derived state changes."""

from dataclasses import dataclass, field

from satel_integra.callbacks import CallbackRegistry


@dataclass
class SatelSubscriptions:
    """Subscriptions notified when message-derived state changes."""

    system_status: CallbackRegistry = field(default_factory=CallbackRegistry)
    zones: CallbackRegistry = field(default_factory=CallbackRegistry)
    outputs: CallbackRegistry = field(default_factory=CallbackRegistry)
