"""Structured result of scanning a Satel Integra panel."""

from dataclasses import dataclass

from .output import SatelOutputInfo
from .panel import SatelPanelInfo
from .partition import SatelPartitionInfo
from .zone import SatelZoneInfo


@dataclass(frozen=True)
class SatelPanelScan:
    """Panel information and discovered configurable devices."""

    panel_info: SatelPanelInfo
    partitions: dict[int, SatelPartitionInfo]
    zones: dict[int, SatelZoneInfo]
    outputs: dict[int, SatelOutputInfo]
