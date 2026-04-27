"""Message classes for communication with Satel Integra panel."""

import logging
from typing import TypeVar
from warnings import warn

from satel_integra.commands import (
    DEPRECATED_QUERY_WRITE_COMMANDS,
    SatelBaseCommand,
    SatelOutboundCommand,
    SatelReadCommand,
    SatelWriteCommand,
)
from satel_integra.const import (
    FRAME_END,
    FRAME_SPECIAL_BYTES,
    FRAME_SPECIAL_BYTES_REPLACEMENT,
    FRAME_START,
)
from satel_integra.models import SatelPanelInfo
from satel_integra.utils import (
    checksum,
    decode_bitmask_le,
    decode_temperature,
    decode_zone_number,
    encode_bitmask_le,
)

_LOGGER = logging.getLogger(__name__)


TCommand = TypeVar("TCommand", bound=SatelBaseCommand)


class SatelBaseMessage[TCommand: SatelBaseCommand]:
    """Base class shared by read/write message types."""

    def __init__(self, cmd: TCommand, msg_data: bytearray) -> None:
        self.cmd = cmd
        self.msg_data = msg_data

    def __str__(self) -> str:
        """Format message string as (SatelMessage) CMD [CMD_HEX] -> DATA_HEX (DATA_LENGTH)"""
        return f"({self.__class__.__name__}) {self.cmd} -> {self.msg_data.hex()} ({len(self.msg_data)})"


class SatelWriteMessage(SatelBaseMessage[SatelOutboundCommand]):
    """Message used to send commands to the panel."""

    def __init__(
        self,
        cmd: SatelOutboundCommand,
        code: str | None = None,
        partitions: list[int] | None = None,
        zones_or_outputs: list[int] | None = None,
        raw_data: bytearray | None = None,
    ) -> None:
        if cmd is SatelReadCommand.RESULT:
            raise ValueError("SatelReadCommand.RESULT cannot be sent as a command")
        if (
            isinstance(cmd, SatelWriteCommand)
            and cmd in DEPRECATED_QUERY_WRITE_COMMANDS
        ):
            replacement = DEPRECATED_QUERY_WRITE_COMMANDS[cmd]
            warn(
                f"{cmd.__class__.__name__}.{cmd.name} is deprecated for query "
                f"commands; use SatelReadCommand.{replacement.name} instead",
                DeprecationWarning,
                stacklevel=2,
            )

        msg_data = bytearray()

        if raw_data is not None:
            msg_data += raw_data
        else:
            if code:
                msg_data += bytearray.fromhex(code.strip().ljust(16, "F"))
            if partitions:
                msg_data += encode_bitmask_le(partitions, 4)
            if zones_or_outputs:
                msg_data += encode_bitmask_le(zones_or_outputs, 32)

        super().__init__(cmd, msg_data)

    def encode_frame(self) -> bytearray:
        """Construct full message frame for sending to panel."""
        data = self.cmd.to_bytearray() + self.msg_data
        csum = checksum(data)
        data.append(csum >> 8)
        data.append(csum & 0xFF)
        data = data.replace(FRAME_SPECIAL_BYTES, FRAME_SPECIAL_BYTES_REPLACEMENT)
        return bytearray(FRAME_START) + data + bytearray(FRAME_END)


class SatelReadMessage(SatelBaseMessage[SatelReadCommand]):
    """Message representing data received from the panel."""

    @staticmethod
    def decode_frame(
        data: bytes,
    ) -> "SatelReadMessage":
        """Verify checksum and strip header/footer of received frame."""
        if data[0:2] != FRAME_START:
            _LOGGER.error("Bad header: %s", data.hex())
            raise ValueError("Invalid frame header")
        if data[-2:] != FRAME_END:
            _LOGGER.error("Bad footer: %s", data.hex())
            raise ValueError("Invalid frame footer")

        output = data[2:-2].replace(
            FRAME_SPECIAL_BYTES_REPLACEMENT, FRAME_SPECIAL_BYTES
        )
        calc_sum = checksum(output[:-2])
        received_sum = (output[-2] << 8) | output[-1]

        if received_sum != calc_sum:
            msg = f"Checksum mismatch: got {received_sum}, expected {calc_sum}"
            _LOGGER.error(
                "Checksum mismatch: get %s, expected %s", received_sum, calc_sum
            )
            raise ValueError(msg)

        cmd_byte, data = output[0], output[1:-2]
        try:
            cmd = SatelReadCommand(cmd_byte)
            match cmd:
                case SatelReadCommand.ZONE_TEMPERATURE:
                    return SatelZoneTemperatureReadMessage(cmd, bytearray(data))
                case SatelReadCommand.INTEGRA_VERSION:
                    return SatelIntegraVersionReadMessage(cmd, bytearray(data))
                case _:
                    return SatelReadMessage(cmd, bytearray(data))
        except ValueError as ex:
            _LOGGER.error("Unknown command byte: %s", hex(cmd_byte))
            raise ValueError("Unknown command byte") from ex

    def get_active_bits(self, expected_length: int) -> list[int]:
        """Convenience wrapper around decode_bitmask_le() for this message."""
        return decode_bitmask_le(self.msg_data, expected_length)


class SatelZoneTemperatureReadMessage(SatelReadMessage):
    """Structured read message for a zone temperature response."""

    def __init__(self, cmd: SatelReadCommand, msg_data: bytearray) -> None:
        super().__init__(cmd, msg_data)

        if len(self.msg_data) != 3:
            err = (
                "Invalid temperature response length: "
                f"expected 3 bytes, got {len(self.msg_data)}"
            )
            raise ValueError(err)

    @property
    def zone_id(self) -> int:
        """Return the decoded zone id for this temperature response."""
        return decode_zone_number(self.msg_data[0])

    @property
    def temperature(self) -> float | None:
        """Return the decoded temperature in Celsius."""
        return decode_temperature(self.msg_data[1], self.msg_data[2])


class SatelIntegraVersionReadMessage(SatelReadMessage):
    """Structured read message for an INTEGRA panel version response."""

    def __init__(self, cmd: SatelReadCommand, msg_data: bytearray) -> None:
        super().__init__(cmd, msg_data)

        if len(self.msg_data) != 14:
            err = (
                "Invalid INTEGRA version response length: "
                f"expected 14 bytes, got {len(self.msg_data)}"
            )
            raise ValueError(err)

    @property
    def panel_info(self) -> SatelPanelInfo:
        """Return parsed INTEGRA panel information."""
        return SatelPanelInfo._from_payload(self.msg_data)
