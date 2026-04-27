"""Commands and responses for Satel Integra protocol."""

from enum import IntEnum, unique


@unique
class SatelBaseCommand(IntEnum):
    """Base class for all Satel commands."""

    def to_bytearray(self) -> bytearray:
        """Return command as single-byte bytearray."""
        return bytearray(self.value.to_bytes(1, "little"))

    def __str__(self) -> str:
        """Format command string as CMD [HEX]"""
        return f"{self.name} [0x{self.value:02X}]"


@unique
class SatelReadCommand(SatelBaseCommand):
    """Read/query commands supported by Satel Integra protocol."""

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
    RTC_AND_STATUS = 0x1A
    PARTITIONS_ARMED_MODE1 = 0x2A
    ZONE_TEMPERATURE = 0x7D
    INTEGRA_VERSION = 0x7E
    READ_DEVICE_NAME = 0xEE
    RESULT = 0xEF


@unique
class SatelWriteCommand(SatelBaseCommand):
    """Action commands supported by Satel Integra protocol."""

    RTC_AND_STATUS = 0x1A
    ZONE_TEMPERATURE = 0x7D
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


SatelOutboundCommand = SatelReadCommand | SatelWriteCommand

DEPRECATED_QUERY_WRITE_COMMANDS: dict[SatelWriteCommand, SatelReadCommand] = {
    SatelWriteCommand.RTC_AND_STATUS: SatelReadCommand.RTC_AND_STATUS,
    SatelWriteCommand.ZONE_TEMPERATURE: SatelReadCommand.ZONE_TEMPERATURE,
    SatelWriteCommand.READ_DEVICE_NAME: SatelReadCommand.READ_DEVICE_NAME,
}


def expected_response_command(command: SatelOutboundCommand) -> SatelReadCommand:
    """Return the response command expected for an outbound command."""
    if isinstance(command, SatelReadCommand):
        if command is SatelReadCommand.RESULT:
            raise ValueError("SatelReadCommand.RESULT cannot be sent as a command")
        return command

    if command in DEPRECATED_QUERY_WRITE_COMMANDS:
        return DEPRECATED_QUERY_WRITE_COMMANDS[command]

    return SatelReadCommand.RESULT
