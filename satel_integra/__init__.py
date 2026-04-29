"""Top-level package for Satel Integra."""

from .exceptions import (
    SatelConnectFailedError,
    SatelConnectionError,
    SatelConnectionInitializationError,
    SatelConnectionSetupError,
    SatelConnectionStoppedError,
    SatelIntegraError,
    SatelPanelBusyError,
    SatelUnexpectedResponseError,
)
from .models import (
    SatelCommunicationModuleInfo,
    SatelDeviceInfo,
    SatelDeviceType,
    SatelFirmwareVersion,
    SatelPanelInfo,
    SatelPanelModel,
    SatelZoneInfo,
)
from .satel_integra import AlarmState, AsyncSatel

__all__ = [
    "AlarmState",
    "AsyncSatel",
    "SatelCommunicationModuleInfo",
    "SatelDeviceInfo",
    "SatelDeviceType",
    "SatelFirmwareVersion",
    "SatelPanelModel",
    "SatelConnectFailedError",
    "SatelConnectionError",
    "SatelConnectionInitializationError",
    "SatelConnectionSetupError",
    "SatelConnectionStoppedError",
    "SatelIntegraError",
    "SatelPanelInfo",
    "SatelPanelBusyError",
    "SatelUnexpectedResponseError",
    "SatelZoneInfo",
]
