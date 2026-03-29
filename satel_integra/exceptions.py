"""Custom exceptions for the Satel Integra library."""


class SatelIntegraError(Exception):
    """Base exception for all library-specific errors."""


class SatelConnectionError(SatelIntegraError):
    """Raised when transport connection setup fails."""


class SatelConnectionStoppedError(SatelConnectionError):
    """Raised when the connection has been terminally stopped."""
