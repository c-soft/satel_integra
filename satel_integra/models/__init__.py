"""Public data models for Satel Integra."""

from .device import SatelDeviceInfo, SatelDeviceKind
from .firmware import SatelFirmwareVersion
from .module import SatelCommunicationModuleInfo
from .panel import SatelPanelInfo, SatelPanelModel
from .zone import SatelZoneInfo

__all__ = [
    "SatelCommunicationModuleInfo",
    "SatelDeviceInfo",
    "SatelDeviceKind",
    "SatelFirmwareVersion",
    "SatelPanelInfo",
    "SatelPanelModel",
    "SatelZoneInfo",
]
