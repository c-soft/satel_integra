"""State derived from incoming panel messages."""

from dataclasses import dataclass, field
from enum import Enum

from satel_integra.models import SatelRtcAndStatus, SatelSystemTroubles


@dataclass
class SatelState:
    """State derived from incoming panel messages."""

    violated_zones: list[int] = field(default_factory=list)
    violated_outputs: list[int] = field(default_factory=list)
    partition_states: dict[Enum, list[int]] = field(default_factory=dict)
    system_status: SatelRtcAndStatus | None = None
    system_troubles: SatelSystemTroubles | None = None
