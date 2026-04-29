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
    SatelOutputInfo,
    SatelPanelInfo,
    SatelPanelModel,
    SatelZoneInfo,
)
from .satel_integra import AlarmState, AsyncSatel

__all__ = [
    "AlarmState",
    "AsyncSatel",
    "SatelCommunicationModuleInfo",
    "SatelConnectFailedError",
    "SatelConnectionError",
    "SatelConnectionInitializationError",
    "SatelConnectionSetupError",
    "SatelConnectionStoppedError",
    "SatelDeviceInfo",
    "SatelDeviceType",
    "SatelFirmwareVersion",
    "SatelIntegraError",
    "SatelOutputInfo",
    "SatelPanelBusyError",
    "SatelPanelInfo",
    "SatelPanelModel",
    "SatelUnexpectedResponseError",
    "SatelZoneInfo",
]
