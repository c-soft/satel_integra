"""Top-level package for Satel Integra."""

from .exceptions import (
    SatelConnectFailedError,
    SatelConnectionError,
    SatelConnectionInitializationError,
    SatelConnectionSetupError,
    SatelConnectionStoppedError,
    SatelEncryptionStateError,
    SatelFrameDecodeError,
    SatelIntegraError,
    SatelMonitoringError,
    SatelMonitoringRejectedError,
    SatelPanelBusyError,
    SatelProtocolError,
    SatelQueueError,
    SatelQueueStoppedError,
    SatelResponseTimeoutError,
    SatelTransportDisconnectedError,
)
from .satel_integra import AlarmState, AsyncSatel

__all__ = [
    "AlarmState",
    "AsyncSatel",
    "SatelConnectFailedError",
    "SatelConnectionError",
    "SatelConnectionInitializationError",
    "SatelConnectionSetupError",
    "SatelConnectionStoppedError",
    "SatelEncryptionStateError",
    "SatelFrameDecodeError",
    "SatelIntegraError",
    "SatelMonitoringError",
    "SatelMonitoringRejectedError",
    "SatelPanelBusyError",
    "SatelProtocolError",
    "SatelQueueError",
    "SatelQueueStoppedError",
    "SatelResponseTimeoutError",
    "SatelTransportDisconnectedError",
]
