"""Structured zone temperature information."""

from dataclasses import dataclass

from satel_integra.utils import decode_device_number, decode_temperature


@dataclass(frozen=True)
class SatelZoneTemperature:
    """Information returned by the zone temperature command."""

    zone_id: int
    temperature: float | None

    @classmethod
    def _from_payload(cls, payload: bytes) -> "SatelZoneTemperature":
        """Parse the 3-byte 0x7D zone temperature response payload."""
        return cls(
            zone_id=decode_device_number(payload[0]),
            temperature=decode_temperature(payload[1], payload[2]),
        )
