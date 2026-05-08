"""Shared device information models."""

from dataclasses import dataclass
from enum import Enum, unique


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
        return payload[3:19].decode("latin-1").rstrip("\x00 ").strip()
