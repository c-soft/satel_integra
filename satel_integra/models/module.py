"""Structured communication module information."""

from dataclasses import dataclass

from .firmware import SatelFirmwareVersion


@dataclass(frozen=True)
class SatelCommunicationModuleInfo:
    """Information returned by the INT-RS/ETHM-1 module version command."""

    firmware: SatelFirmwareVersion
    supports_256_zones_outputs: bool
    supports_trouble_memory_part_8: bool
    supports_arm_no_bypass: bool

    @classmethod
    def _from_payload(cls, payload: bytes) -> "SatelCommunicationModuleInfo":
        """Parse the 12-byte 0x7C module version response payload."""
        capabilities = payload[11]

        return cls(
            firmware=SatelFirmwareVersion._from_payload(payload[:11]),
            supports_256_zones_outputs=bool(capabilities & 0b0000_0001),
            supports_trouble_memory_part_8=bool(capabilities & 0b0000_0010),
            supports_arm_no_bypass=bool(capabilities & 0b0000_0100),
        )
