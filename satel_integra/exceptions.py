"""Custom exceptions for the Satel Integra library."""


class SatelIntegraError(Exception):
    """Base exception for all library-specific errors."""


class SatelConnectionError(SatelIntegraError):
    """Raised when transport connection setup fails."""


class SatelConnectFailedError(SatelConnectionError):
    """Raised when the TCP connection to the panel cannot be established."""


class SatelConnectionSetupError(SatelConnectionError):
    """Raised when a TCP connection cannot be prepared for use."""


class SatelPanelBusyError(SatelConnectionSetupError):
    """Raised when the panel session is occupied by another client."""


class SatelConnectionInitializationError(SatelConnectionSetupError):
    """Raised when the panel does not complete connection setup successfully."""


class SatelConnectionStoppedError(SatelConnectionError):
    """Raised when the connection has been terminally stopped."""


class SatelUnexpectedResponseError(SatelIntegraError):
    """Raised when a panel response does not match the requested command."""
