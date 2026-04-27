"""Satel firmware information."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class SatelFirmwareVersion:
    """Firmware version parsed from the Satel protocol version payload."""

    version: str
    release_date: date

    @classmethod
    def _from_payload(cls, payload: bytes) -> "SatelFirmwareVersion":
        """Parse an 11-byte Satel firmware payload like b'12320120527'."""
        if len(payload) != 11:
            msg = f"Invalid firmware version length: expected 11 bytes, got {len(payload)}"
            raise ValueError(msg)

        raw_version = payload.decode("ascii")
        if not raw_version.isdigit():
            msg = f"Invalid firmware version payload: {raw_version!r}"
            raise ValueError(msg)

        version = f"{int(raw_version[0])}.{int(raw_version[1:3]):02d}"
        release_date = date(
            int(raw_version[3:7]),
            int(raw_version[7:9]),
            int(raw_version[9:11]),
        )

        return cls(version=version, release_date=release_date)
