"""State derived from incoming panel messages."""

from dataclasses import dataclass, field
from enum import Enum, unique

from satel_integra.models import SatelPanelInfo, SatelRtcAndStatus, SatelSystemTroubles

_MAX_ZONES = 256
_MAX_OUTPUTS = 256


def _default_zone_states() -> dict[int, "SatelZoneState"]:
    return {zone: SatelZoneState() for zone in range(1, _MAX_ZONES + 1)}


def _default_output_states() -> dict[int, "SatelOutputState"]:
    return {output: SatelOutputState() for output in range(1, _MAX_OUTPUTS + 1)}


@unique
class AlarmState(Enum):
    """Represent the status of the alarm."""

    ARMED_MODE0 = 0
    ARMED_MODE1 = 1
    ARMED_MODE2 = 2
    ARMED_MODE3 = 3
    ARMED_SUPPRESSED = 4
    ENTRY_TIME = 5
    EXIT_COUNTDOWN_OVER_10 = 6
    EXIT_COUNTDOWN_UNDER_10 = 7
    TRIGGERED = 8
    TRIGGERED_FIRE = 9
    DISARMED = 10


@dataclass
class SatelZoneState:
    """State of a zone derived from incoming panel messages."""

    violated: bool | None = None
    tampered: bool | None = None


@dataclass
class SatelOutputState:
    """State of an output derived from incoming panel messages."""

    active: bool | None = None


@dataclass
class SatelState:
    """State derived from incoming panel messages."""

    zones: dict[int, SatelZoneState] = field(default_factory=_default_zone_states)
    outputs: dict[int, SatelOutputState] = field(default_factory=_default_output_states)
    partition_states: dict[AlarmState, list[int]] = field(default_factory=dict)
    system_status: SatelRtcAndStatus | None = None
    system_troubles: SatelSystemTroubles | None = None
    panel_info: SatelPanelInfo | None = None

    def configure_for_panel(self, panel_info: SatelPanelInfo) -> None:
        """Store panel information and size device state for a known model."""
        self.panel_info = panel_info
        if panel_info.model is None:
            return

        self._resize_zones(panel_info.model.max_zones)
        self._resize_outputs(panel_info.model.max_outputs)

    def _resize_zones(self, capacity: int) -> None:
        """Resize zone state while preserving existing entries."""
        self.zones = {
            zone: self.zones.get(zone, SatelZoneState())
            for zone in range(1, capacity + 1)
        }

    def _resize_outputs(self, capacity: int) -> None:
        """Resize output state while preserving existing entries."""
        self.outputs = {
            output: self.outputs.get(output, SatelOutputState())
            for output in range(1, capacity + 1)
        }
