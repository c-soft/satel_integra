"""Bitmask response model."""

from dataclasses import dataclass
from typing import Self

from satel_integra.utils import decode_bitmask_le


@dataclass(frozen=True)
class SatelBitmaskData:
    """Represent active positions in a response bitmask."""

    active: frozenset[int]

    @classmethod
    def _from_payload(cls, payload: bytes) -> Self:
        """Decode active one-based positions from a response payload."""
        return cls(frozenset(decode_bitmask_le(payload, len(payload))))
