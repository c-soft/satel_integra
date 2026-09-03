"""Public data models for Satel Integra."""

from .device import SatelDeviceInfo, SatelDeviceType
from .firmware import SatelFirmwareVersion
from .module import SatelCommunicationModuleInfo
from .output import SatelOutputInfo
from .panel import SatelPanelInfo, SatelPanelModel
from .partition import SatelPartitionInfo
from .result import SatelCommandResult, SatelResultCode
from .temperature import SatelZoneTemperature
from .zone import SatelZoneInfo

__all__ = [
    "SatelCommandResult",
    "SatelCommunicationModuleInfo",
    "SatelDeviceInfo",
    "SatelDeviceType",
    "SatelFirmwareVersion",
    "SatelOutputInfo",
    "SatelPanelInfo",
    "SatelPanelModel",
    "SatelPartitionInfo",
    "SatelResultCode",
    "SatelZoneInfo",
    "SatelZoneTemperature",
]
