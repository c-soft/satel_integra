"""Public data models for Satel Integra."""

from .firmware import SatelFirmwareVersion
from .panel import SatelPanelInfo, SatelPanelModel

__all__ = [
    "SatelFirmwareVersion",
    "SatelPanelInfo",
    "SatelPanelModel",
]
