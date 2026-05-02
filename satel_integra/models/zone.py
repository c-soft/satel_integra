"""Structured zone information."""

from dataclasses import dataclass, field

from satel_integra.utils import decode_device_number

from .device import SatelDeviceInfo, SatelDeviceType


@dataclass(frozen=True)
class SatelZoneInfo(SatelDeviceInfo):
    """Information returned by the 0xEE zone name read command."""

    device_type: SatelDeviceType = field(default=SatelDeviceType.ZONE, init=False)
    type_code: int
    partition_assignment: int | None

    @classmethod
    def _from_payload(cls, payload: bytes) -> "SatelZoneInfo":
        """Parse a 0xEE zone payload with partition assignment."""
        return cls(
            device_number=decode_device_number(payload[1]),
            name=cls._decode_name(payload),
            type_code=payload[2],
            partition_assignment=payload[19] if payload[19] else None,
        )
