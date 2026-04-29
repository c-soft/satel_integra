"""Structured zone information."""

from dataclasses import dataclass, field

from satel_integra.utils import decode_zone_number

from .device import SatelDeviceInfo, SatelDeviceKind


@dataclass(frozen=True)
class SatelZoneInfo(SatelDeviceInfo):
    """Information returned by the 0xEE zone name read command."""

    kind: SatelDeviceKind = field(default=SatelDeviceKind.ZONE, init=False)
    type_code: int
    partition_assignment: int | None

    @classmethod
    def _from_payload(cls, payload: bytes) -> "SatelZoneInfo":
        """Parse a 0xEE zone payload with partition assignment."""
        return cls(
            number=decode_zone_number(payload[1]),
            name=cls._decode_name(payload),
            type_code=payload[2],
            partition_assignment=payload[19] if payload[19] else None,
        )
