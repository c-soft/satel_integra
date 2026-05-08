"""Public data models for Satel Integra."""

from .device import SatelDeviceInfo, SatelDeviceType
from .firmware import SatelFirmwareVersion
from .module import SatelCommunicationModuleInfo
from .output import SatelOutputInfo
from .panel import SatelPanelInfo, SatelPanelModel
from .partition import SatelPartitionInfo
from .zone import SatelZoneInfo

__all__ = [
    "SatelCommunicationModuleInfo",
    "SatelDeviceInfo",
    "SatelDeviceType",
    "SatelFirmwareVersion",
    "SatelOutputInfo",
    "SatelPanelInfo",
    "SatelPanelModel",
    "SatelPartitionInfo",
    "SatelZoneInfo",
]
