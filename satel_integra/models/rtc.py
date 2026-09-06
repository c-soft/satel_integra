"""Structured basic panel status information."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SatelRtcAndStatus:
    """Basic panel status returned by command 0x1A."""

    service_mode: bool
    troubles: bool
    acu_100_present: bool
    int_rx_present: bool
    troubles_memory: bool
    grade_2_or_3: bool

    @classmethod
    def _from_payload(cls, payload: bytes) -> "SatelRtcAndStatus":
        """Parse the status flags from the nine-byte RTC/status payload."""
        return cls(
            service_mode=bool(payload[7] & 0x80),
            troubles=bool(payload[7] & 0x40),
            acu_100_present=bool(payload[8] & 0x80),
            int_rx_present=bool(payload[8] & 0x40),
            troubles_memory=bool(payload[8] & 0x20),
            grade_2_or_3=bool(payload[8] & 0x10),
        )
