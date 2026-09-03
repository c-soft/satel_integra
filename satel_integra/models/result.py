"""Structured command results returned by the INTEGRA panel."""

from dataclasses import dataclass
from enum import IntEnum, unique


@unique
class SatelResultCode(IntEnum):
    """Known wire-level result codes returned by protocol command 0xEF."""

    OK = 0x00
    USER_CODE_NOT_FOUND = 0x01
    NO_ACCESS = 0x02
    USER_DOES_NOT_EXIST = 0x03
    USER_ALREADY_EXISTS = 0x04
    WRONG_CODE_OR_CODE_ALREADY_EXISTS = 0x05
    TELEPHONE_CODE_ALREADY_EXISTS = 0x06
    CHANGED_CODE_IS_THE_SAME = 0x07
    OTHER_ERROR = 0x08
    CANNOT_ARM_CAN_FORCE = 0x11
    CANNOT_ARM = 0x12
    COMMAND_ACCEPTED = 0xFF


@dataclass(frozen=True)
class SatelCommandResult:
    """Decoded 0xEF command result, including unknown raw result codes."""

    code: SatelResultCode | int

    @property
    def is_success(self) -> bool:
        """Return whether the panel accepted or completed the command."""
        return self.code in (SatelResultCode.OK, SatelResultCode.COMMAND_ACCEPTED)

    @classmethod
    def _from_payload(cls, payload: bytes) -> "SatelCommandResult":
        """Parse the one-byte 0xEF response payload."""
        raw_code = payload[0]
        try:
            code: SatelResultCode | int = SatelResultCode(raw_code)
        except ValueError:
            code = raw_code
        return cls(code=code)
