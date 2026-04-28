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
    SatelFirmwareVersion,
    SatelPanelInfo,
    SatelPanelModel,
)
from .satel_integra import AlarmState, AsyncSatel

__all__ = [
    "AlarmState",
    "AsyncSatel",
    "SatelCommunicationModuleInfo",
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
]
