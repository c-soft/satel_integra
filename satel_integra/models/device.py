"""Shared device information models."""

from dataclasses import dataclass
from enum import Enum, unique


@unique
class SatelDeviceKind(Enum):
    """Semantic device kinds returned by 0xEE reads."""

    PARTITION = "partition"
    ZONE = "zone"
    USER = "user"
    EXPANDER_OR_LCD = "expander_or_lcd"
    OUTPUT = "output"


@dataclass(frozen=True)
class SatelDeviceInfo:
    """Shared fields present in 0xEE device information responses."""

    kind: SatelDeviceKind
    number: int
    name: str

    @staticmethod
    def _decode_name(payload: bytes) -> str:
        """Decode the shared 0xEE device name field."""
        return payload[3:19].decode("latin-1").rstrip("\x00 ").strip()
