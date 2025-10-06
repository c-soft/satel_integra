"""Commands and responses for Satel Integra protocol."""

from enum import IntEnum, unique


@unique
class SatelBaseCommand(IntEnum):
    """Base class for all Satel commands."""

    def to_bytearray(self) -> bytearray:
        """Return command as single-byte bytearray."""
        return bytearray(self.value.to_bytes(1, "little"))

    def __str__(self) -> str:
        return f"{self.name} (0x{self.value:02X})"


@unique
class SatelReadCommand(SatelBaseCommand):
    """Read commands supported by Satel Integra protocol."""

    ZONES_VIOLATED = 0x00
    PARTITIONS_ARMED_SUPPRESSED = 0x09
    PARTITIONS_ARMED_MODE0 = 0x0A
    PARTITIONS_ARMED_MODE2 = 0x0B
    PARTITIONS_ARMED_MODE3 = 0x0C
    PARTITIONS_ENTRY_TIME = 0x0E
    PARTITIONS_EXIT_COUNTDOWN_OVER_10 = 0x0F
    PARTITIONS_EXIT_COUNTDOWN_UNDER_10 = 0x10
    PARTITIONS_ALARM = 0x13
    PARTITIONS_FIRE_ALARM = 0x14
    OUTPUTS_STATE = 0x17
    PARTITIONS_ARMED_MODE1 = 0x2A
    READ_DEVICE_NAME = 0xEE
    RESULT = 0xEF


@unique
class SatelWriteCommand(SatelBaseCommand):
    """Write commands supported by Satel Integra protocol."""

    START_MONITORING = 0x7F
    PARTITIONS_ARM_MODE_0 = 0x80
    PARTITIONS_ARM_MODE_1 = 0x81
    PARTITIONS_ARM_MODE_2 = 0x82
    PARTITIONS_ARM_MODE_3 = 0x83
    PARTITIONS_DISARM = 0x84
    PARTITIONS_CLEAR_ALARM = 0x85
    OUTPUTS_ON = 0x88
    OUTPUTS_OFF = 0x89
    READ_DEVICE_NAME = 0xEE
