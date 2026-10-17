"""State derived from incoming panel messages."""

from dataclasses import dataclass, field
from enum import Enum, unique

from satel_integra.models import SatelRtcAndStatus, SatelSystemTroubles


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
class SatelState:
    """State derived from incoming panel messages."""

    violated_zones: list[int] = field(default_factory=list)
    violated_outputs: list[int] = field(default_factory=list)
    partition_states: dict[AlarmState, list[int]] = field(default_factory=dict)
    system_status: SatelRtcAndStatus | None = None
    system_troubles: SatelSystemTroubles | None = None
