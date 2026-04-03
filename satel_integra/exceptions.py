"""Custom exceptions for the Satel Integra library."""


class SatelIntegraError(Exception):
    """Base exception for all library-specific errors."""


class SatelConnectionError(SatelIntegraError):
    """Raised when transport connection setup fails."""


class SatelConnectFailedError(SatelConnectionError):
    """Raised when the TCP connection to the panel cannot be established."""


class SatelTransportDisconnectedError(SatelConnectionError):
    """Raised when an established transport connection is lost or unusable."""


class SatelConnectionSetupError(SatelConnectionError):
    """Raised when a TCP connection cannot be prepared for use."""


class SatelPanelBusyError(SatelConnectionSetupError):
    """Raised when the panel session is occupied by another client."""


class SatelConnectionInitializationError(SatelConnectionSetupError):
    """Raised when the panel does not complete connection setup successfully."""


class SatelConnectionStoppedError(SatelConnectionError):
    """Raised when the connection has been terminally stopped."""


class SatelMonitoringError(SatelIntegraError):
    """Raised when monitoring setup does not complete successfully."""


class SatelMonitoringRejectedError(SatelMonitoringError):
    """Raised when the panel rejects a monitoring setup request."""


class SatelProtocolError(SatelIntegraError):
    """Base exception for malformed or incompatible protocol data."""


class SatelFrameDecodeError(SatelProtocolError):
    """Raised when a received frame cannot be decoded successfully."""


class SatelEncryptionStateError(SatelProtocolError):
    """Raised when encrypted session state is invalid or inconsistent."""


class SatelQueueError(SatelIntegraError):
    """Base exception for queue-related failures."""


class SatelQueueStoppedError(SatelQueueError):
    """Raised when a message is queued after the queue has stopped."""


class SatelResponseTimeoutError(SatelQueueError):
    """Raised when the panel does not return a response before the timeout."""
