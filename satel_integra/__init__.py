"""Top-level package for Satel Integra."""

from .exceptions import (
    SatelCommandRejectedError,
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
    SatelCommandResult,
    SatelCommunicationModuleInfo,
    SatelDeviceInfo,
    SatelDeviceType,
    SatelFirmwareVersion,
    SatelOutputInfo,
    SatelPanelInfo,
    SatelPanelModel,
    SatelPartitionInfo,
    SatelResultCode,
    SatelZoneInfo,
)
from .satel_integra import AlarmState, AsyncSatel

__all__ = [
    "AlarmState",
    "AsyncSatel",
    "SatelCommandRejectedError",
    "SatelCommandResult",
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
    "SatelPartitionInfo",
    "SatelResultCode",
    "SatelUnexpectedResponseError",
    "SatelZoneInfo",
]
