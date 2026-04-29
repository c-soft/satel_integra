"""Public data models for Satel Integra."""

from .firmware import SatelFirmwareVersion
from .module import SatelCommunicationModuleInfo
from .panel import SatelPanelInfo, SatelPanelModel

__all__ = [
    "SatelCommunicationModuleInfo",
    "SatelFirmwareVersion",
    "SatelPanelInfo",
    "SatelPanelModel",
]
