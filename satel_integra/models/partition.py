"""Structured partition information."""

from dataclasses import dataclass, field

from .device import SatelDeviceInfo, SatelDeviceType


@dataclass(frozen=True)
class SatelPartitionInfo(SatelDeviceInfo):
    """Information returned by the 0xEE partition read command."""

    device_type: SatelDeviceType = field(default=SatelDeviceType.PARTITION, init=False)
    type_code: int
    object_assignment: int | None

    @classmethod
    def _from_payload(cls, payload: bytes) -> "SatelPartitionInfo":
        """Parse a 0xEE partition payload with object assignment."""
        return cls(
            device_number=payload[1],
            name=cls._decode_name(payload),
            type_code=payload[2],
            object_assignment=payload[19] if payload[19] else None,
        )
