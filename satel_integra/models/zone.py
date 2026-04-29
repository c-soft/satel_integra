"""Structured zone information."""

from dataclasses import dataclass

from satel_integra.utils import decode_zone_number


@dataclass(frozen=True)
class SatelZoneInfo:
    """Information returned by the 0xEE zone name read command."""

    number: int
    name: str
    type_code: int
    partition_assignment: int | None

    @classmethod
    def _from_payload(cls, payload: bytes) -> "SatelZoneInfo":
        """Parse a 0xEE zone payload with partition assignment."""
        name = payload[3:19].decode("latin-1").rstrip("\x00 ").strip()

        return cls(
            number=decode_zone_number(payload[1]),
            name=name,
            type_code=payload[2],
            partition_assignment=payload[19] if payload[19] else None,
        )
