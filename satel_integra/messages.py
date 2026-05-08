"""Message classes for communication with Satel Integra panel."""

import logging
from enum import IntEnum, unique
from functools import cached_property
from typing import ClassVar, TypeVar
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
from satel_integra.exceptions import SatelUnexpectedResponseError
from satel_integra.models import (
    SatelCommunicationModuleInfo,
    SatelOutputInfo,
    SatelPanelInfo,
    SatelPartitionInfo,
    SatelZoneInfo,
)
from satel_integra.utils import (
    checksum,
    decode_bitmask_le,
    decode_device_number,
    decode_temperature,
    encode_bitmask_le,
)

_LOGGER = logging.getLogger(__name__)


TCommand = TypeVar("TCommand", bound=SatelBaseCommand)


@unique
class SatelDeviceSelector(IntEnum):
    """Raw 0xEE device selectors used on the wire."""

    OUTPUT = 0x04
    ZONE_WITH_PARTITION_ASSIGNMENT = 0x05
    PARTITION_WITH_OBJECT_ASSIGNMENT = 0x10


def _decode_device_read_message(
    cmd: SatelReadCommand, msg_data: bytearray
) -> "SatelReadMessage":
    """Decode a 0xEE device response into the appropriate message type."""
    if not msg_data:
        raise SatelUnexpectedResponseError(
            "READ_DEVICE_NAME response missing device type"
        )

    match msg_data[0]:
        case SatelDeviceSelector.PARTITION_WITH_OBJECT_ASSIGNMENT:
            return SatelPartitionInfoReadMessage(cmd, msg_data)
        case SatelDeviceSelector.OUTPUT:
            return SatelOutputInfoReadMessage(cmd, msg_data)
        case SatelDeviceSelector.ZONE_WITH_PARTITION_ASSIGNMENT:
            return SatelZoneInfoReadMessage(cmd, msg_data)

    _LOGGER.debug(
        "Unsupported READ_DEVICE_NAME device type: 0x%02X; using default read message",
        msg_data[0],
    )
    return SatelReadMessage(cmd, msg_data)


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

    expected_data_length: ClassVar[int | None] = None

    def __init__(self, cmd: SatelReadCommand, msg_data: bytearray) -> None:
        super().__init__(cmd, msg_data)
        self._validate_data_length()

    @staticmethod
    def decode_frame(
        data: bytes,
    ) -> "SatelReadMessage | None":
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
        except ValueError:
            _LOGGER.warning(
                "Ignoring unknown command byte: %s (payload=%s)",
                hex(cmd_byte),
                output.hex(),
            )
            return None

        _LOGGER.debug("Received command: %s", cmd)

        match cmd:
            case SatelReadCommand.MODULE_VERSION:
                return SatelModuleVersionReadMessage(cmd, bytearray(data))
            case SatelReadCommand.ZONE_TEMPERATURE:
                return SatelZoneTemperatureReadMessage(cmd, bytearray(data))
            case SatelReadCommand.INTEGRA_VERSION:
                return SatelIntegraVersionReadMessage(cmd, bytearray(data))
            case SatelReadCommand.READ_EVENT:
                _LOGGER.debug(
                    "Received event message; event decoding is not implemented: %s",
                    data.hex(),
                )
                return SatelReadMessage(cmd, bytearray(data))
            case SatelReadCommand.READ_DEVICE_NAME:
                return _decode_device_read_message(cmd, bytearray(data))
            case _:
                return SatelReadMessage(cmd, bytearray(data))

    def get_active_bits(self, expected_length: int) -> list[int]:
        """Convenience wrapper around decode_bitmask_le() for this message."""
        return decode_bitmask_le(self.msg_data, expected_length)

    def _validate_data_length(self) -> None:
        """Validate fixed-length structured responses."""
        if self.expected_data_length is None:
            return

        if len(self.msg_data) == self.expected_data_length:
            return

        err = (
            f"Invalid response length for {self.cmd}: "
            f"expected {self.expected_data_length} bytes, got {len(self.msg_data)} "
            f"(payload={self.msg_data.hex()})"
        )
        _LOGGER.warning(err)
        raise SatelUnexpectedResponseError(err)


class SatelZoneTemperatureReadMessage(SatelReadMessage):
    """Structured read message for a zone temperature response."""

    expected_data_length = 3

    @cached_property
    def zone_id(self) -> int:
        """Return the decoded zone id for this temperature response."""
        return decode_device_number(self.msg_data[0])

    @cached_property
    def temperature(self) -> float | None:
        """Return the decoded temperature in Celsius."""
        return decode_temperature(self.msg_data[1], self.msg_data[2])


class SatelModuleVersionReadMessage(SatelReadMessage):
    """Structured read message for an INT-RS/ETHM-1 module version response."""

    expected_data_length = 12

    @cached_property
    def module_info(self) -> SatelCommunicationModuleInfo:
        """Return parsed communication module information."""
        return SatelCommunicationModuleInfo._from_payload(self.msg_data)


class SatelIntegraVersionReadMessage(SatelReadMessage):
    """Structured read message for an INTEGRA panel version response."""

    expected_data_length = 14

    @cached_property
    def panel_info(self) -> SatelPanelInfo:
        """Return parsed INTEGRA panel information."""
        return SatelPanelInfo._from_payload(self.msg_data)


class SatelZoneInfoReadMessage(SatelReadMessage):
    """Structured read message for a 0xEE zone info response."""

    expected_data_length = 20

    @cached_property
    def device_info(self) -> SatelZoneInfo:
        """Return parsed zone information."""
        return SatelZoneInfo._from_payload(self.msg_data)


class SatelPartitionInfoReadMessage(SatelReadMessage):
    """Structured read message for a 0xEE partition info response."""

    expected_data_length = 20

    @cached_property
    def device_info(self) -> SatelPartitionInfo:
        """Return parsed partition information."""
        return SatelPartitionInfo._from_payload(self.msg_data)


class SatelOutputInfoReadMessage(SatelReadMessage):
    """Structured read message for a 0xEE output info response."""

    expected_data_length = 19

    @cached_property
    def device_info(self) -> SatelOutputInfo:
        """Return parsed output information."""
        return SatelOutputInfo._from_payload(self.msg_data)
