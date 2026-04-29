"""Structured output information."""

from dataclasses import dataclass, field

from satel_integra.utils import decode_device_number

from .device import SatelDeviceInfo, SatelDeviceType


@dataclass(frozen=True)
class SatelOutputInfo(SatelDeviceInfo):
    """Information returned by the 0xEE output name read command."""

    device_type: SatelDeviceType = field(default=SatelDeviceType.OUTPUT, init=False)
    type_code: int

    @classmethod
    def _from_payload(cls, payload: bytes) -> "SatelOutputInfo":
        """Parse a 0xEE output payload."""
        return cls(
            device_number=decode_device_number(payload[1]),
            name=cls._decode_name(payload),
            type_code=payload[2],
        )
