"""Satel firmware information."""

import logging
from dataclasses import dataclass
from datetime import date

from satel_integra.exceptions import SatelUnexpectedResponseError

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SatelFirmwareVersion:
    """Firmware version parsed from the Satel protocol version payload."""

    version: str
    release_date: date

    @classmethod
    def _from_payload(cls, payload: bytes) -> "SatelFirmwareVersion":
        """Parse an 11-byte Satel firmware payload like b'12320120527'."""
        try:
            raw_version = payload.decode("ascii")
        except UnicodeDecodeError as err:
            msg = f"Invalid firmware version payload encoding: {payload.hex()}"
            _LOGGER.warning(msg)
            raise SatelUnexpectedResponseError(msg) from err

        if not raw_version.isdigit():
            msg = f"Invalid firmware version payload: {raw_version!r}"
            _LOGGER.warning(msg)
            raise SatelUnexpectedResponseError(msg)

        version = f"{int(raw_version[0])}.{int(raw_version[1:3]):02d}"
        try:
            release_date = date(
                int(raw_version[3:7]),
                int(raw_version[7:9]),
                int(raw_version[9:11]),
            )
        except ValueError as err:
            msg = f"Invalid firmware release date payload: {raw_version!r}"
            _LOGGER.warning(msg)
            raise SatelUnexpectedResponseError(msg) from err

        return cls(version=version, release_date=release_date)
