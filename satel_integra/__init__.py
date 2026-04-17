"""Top-level package for Satel Integra."""

from .exceptions import (
    SatelConnectFailedError,
    SatelConnectionError,
    SatelConnectionInitializationError,
    SatelConnectionSetupError,
    SatelConnectionStoppedError,
    SatelIntegraError,
    SatelPanelBusyError,
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
    "SatelIntegraError",
    "SatelPanelBusyError",
]
