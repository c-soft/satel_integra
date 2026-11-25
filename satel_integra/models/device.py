"""Shared device information models."""

from dataclasses import dataclass
from enum import Enum, unique

DEFAULT_DEVICE_NAME_ENCODING = "latin-1"

# INTEGRA protocol Appendix 5. Keep the existing codec for every language.
DEVICE_NAME_ENCODING_BY_LANGUAGE_CODE = dict.fromkeys(
    (*range(31), 255), DEFAULT_DEVICE_NAME_ENCODING
)


@unique
class SatelDeviceType(Enum):
    """Semantic device types returned by 0xEE reads."""

    PARTITION = "partition"
    OUTPUT = "output"
    ZONE = "zone"


@dataclass(frozen=True)
class SatelDeviceInfo:
    """Shared fields present in 0xEE device information responses."""

    device_type: SatelDeviceType
    device_number: int
    name: str

    @staticmethod
    def _decode_name(payload: bytes) -> str:
        """Decode the shared 0xEE device name field."""
        return (
            payload[3:19].decode(DEFAULT_DEVICE_NAME_ENCODING).rstrip("\x00 ").strip()
        )
