"""Message classes for communication with Satel Integra panel."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum, unique
from functools import cached_property
from typing import Protocol, Self, TypeVar
from warnings import warn

from satel_integra.commands import (
    DEPRECATED_QUERY_WRITE_COMMANDS,
    MERGEABLE_WRITE_COMMANDS,
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
    SatelCommandResult,
    SatelCommunicationModuleInfo,
    SatelOutputInfo,
    SatelPanelInfo,
    SatelPartitionInfo,
    SatelRtcAndStatus,
    SatelZoneInfo,
    SatelZoneTemperature,
)
from satel_integra.utils import (
    checksum,
    decode_bitmask_le,
    encode_bitmask_le,
)

_LOGGER = logging.getLogger(__name__)


TCommand = TypeVar("TCommand", bound=SatelBaseCommand)


class SatelReadMessageData(Protocol):
    """Protocol for typed decoded read message data."""

    @classmethod
    def _from_payload(cls, payload: bytes) -> Self:
        """Parse a raw response payload into typed data."""
        ...


@dataclass(frozen=True)
class ReadCommandSpec:
    """Defines how a read command response should be constructed."""

    command: SatelReadCommand
    data_type: type[SatelReadMessageData] | None = None
    expected_data_lengths: tuple[int, ...] | None = None
    decoder: Callable[[SatelReadCommand, bytearray], "SatelReadMessage"] | None = None

    def construct(
        self, cmd: SatelReadCommand, msg_data: bytearray
    ) -> "SatelReadMessage":
        """Construct a read message for this command spec."""
        if self.decoder is not None:
            return self.decoder(cmd, msg_data)

        if self.data_type is not None:
            return SatelTypedReadMessage(
                cmd,
                msg_data,
                data_type=self.data_type,
                expected_data_lengths=self.expected_data_lengths,
            )

        return SatelReadMessage(
            cmd,
            msg_data,
            expected_data_lengths=self.expected_data_lengths,
        )


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

    try:
        selector = SatelDeviceSelector(msg_data[0])
    except ValueError:
        _LOGGER.debug(
            "Unsupported READ_DEVICE_NAME device type: 0x%02X",
            msg_data[0],
        )
        return SatelReadMessage(cmd, msg_data)

    return READ_DEVICE_NAME_SPECS[selector].construct(cmd, msg_data)


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

        self._code = code
        self._partitions = set(partitions or ())
        self._zones_or_outputs = set(zones_or_outputs or ())
        self._is_raw = raw_data is not None

        if raw_data is not None:
            msg_data = bytearray(raw_data)
        else:
            msg_data = self._encode_payload()

        super().__init__(cmd, msg_data)

    def _encode_payload(self) -> bytearray:
        """Encode code and device bitmasks into the message payload."""
        msg_data = bytearray()
        if self._code:
            msg_data += bytearray.fromhex(self._code.strip().ljust(16, "F"))
        if self._partitions:
            msg_data += encode_bitmask_le(sorted(self._partitions), 4)
        if self._zones_or_outputs:
            msg_data += encode_bitmask_le(sorted(self._zones_or_outputs), 32)
        return msg_data

    def can_merge(self, other: "SatelWriteMessage") -> bool:
        """Return True if other can be grouped into this message."""
        return (
            not self._is_raw
            and not other._is_raw
            and self.cmd is other.cmd
            and self.cmd in MERGEABLE_WRITE_COMMANDS
            and self._code == other._code
        )

    def merge(self, other: "SatelWriteMessage") -> None:
        """Group the devices of other into this message."""
        if not self.can_merge(other):
            raise ValueError(f"Cannot merge {other} into {self}")

        self._partitions |= other._partitions
        self._zones_or_outputs |= other._zones_or_outputs
        self.msg_data = self._encode_payload()

    def overlaps(self, other: "SatelWriteMessage") -> bool:
        """Return True if both messages address any of the same devices."""
        return bool(
            self._partitions & other._partitions
            or self._zones_or_outputs & other._zones_or_outputs
        )

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

    def __init__(
        self,
        cmd: SatelReadCommand,
        msg_data: bytearray,
        *,
        expected_data_lengths: tuple[int, ...] | None = None,
    ) -> None:
        super().__init__(cmd, msg_data)
        self._expected_data_lengths = expected_data_lengths
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

        spec = READ_COMMAND_SPECS.get(cmd)
        if spec is not None:
            return spec.construct(cmd, bytearray(data))

        if cmd is SatelReadCommand.READ_EVENT:
            _LOGGER.debug(
                "Received event message; event decoding is not implemented: %s",
                data.hex(),
            )

        return SatelReadMessage(cmd, bytearray(data))

    def get_active_bits(self, expected_length: int) -> list[int]:
        """Convenience wrapper around decode_bitmask_le() for this message."""
        return decode_bitmask_le(self.msg_data, expected_length)

    def _validate_data_length(self) -> None:
        """Validate fixed-length structured responses."""
        expected_data_lengths = self._expected_data_lengths
        if expected_data_lengths is None:
            return

        if len(self.msg_data) in expected_data_lengths:
            return

        if len(expected_data_lengths) == 1:
            expected = f"{expected_data_lengths[0]} bytes"
        else:
            expected = f"one of {expected_data_lengths} bytes"

        err = (
            f"Invalid response length for {self.cmd}: "
            f"expected {expected}, got {len(self.msg_data)} "
            f"(payload={self.msg_data.hex()})"
        )
        _LOGGER.warning(err)
        raise SatelUnexpectedResponseError(err)


class SatelTypedReadMessage[TData: SatelReadMessageData](SatelReadMessage):
    """Read message that exposes its decoded payload as typed data."""

    def __init__(
        self,
        cmd: SatelReadCommand,
        msg_data: bytearray,
        *,
        data_type: type[TData],
        expected_data_lengths: tuple[int, ...] | None = None,
    ) -> None:
        self.data_type = data_type
        super().__init__(
            cmd,
            msg_data,
            expected_data_lengths=expected_data_lengths,
        )

    @cached_property
    def data(self) -> TData:
        """Return the decoded response data."""
        return self.data_type._from_payload(self.msg_data)


READ_DEVICE_NAME_SPECS: dict[SatelDeviceSelector, ReadCommandSpec] = {
    SatelDeviceSelector.PARTITION_WITH_OBJECT_ASSIGNMENT: ReadCommandSpec(
        command=SatelReadCommand.READ_DEVICE_NAME,
        data_type=SatelPartitionInfo,
        expected_data_lengths=(20,),
    ),
    SatelDeviceSelector.OUTPUT: ReadCommandSpec(
        command=SatelReadCommand.READ_DEVICE_NAME,
        data_type=SatelOutputInfo,
        expected_data_lengths=(19,),
    ),
    SatelDeviceSelector.ZONE_WITH_PARTITION_ASSIGNMENT: ReadCommandSpec(
        command=SatelReadCommand.READ_DEVICE_NAME,
        data_type=SatelZoneInfo,
        expected_data_lengths=(20,),
    ),
}


READ_COMMAND_SPECS: dict[SatelReadCommand, ReadCommandSpec] = {
    SatelReadCommand.RTC_AND_STATUS: ReadCommandSpec(
        command=SatelReadCommand.RTC_AND_STATUS,
        data_type=SatelRtcAndStatus,
        expected_data_lengths=(9,),
    ),
    SatelReadCommand.RESULT: ReadCommandSpec(
        command=SatelReadCommand.RESULT,
        data_type=SatelCommandResult,
        expected_data_lengths=(1,),
    ),
    SatelReadCommand.MODULE_VERSION: ReadCommandSpec(
        command=SatelReadCommand.MODULE_VERSION,
        data_type=SatelCommunicationModuleInfo,
        expected_data_lengths=(12,),
    ),
    SatelReadCommand.ZONE_TEMPERATURE: ReadCommandSpec(
        command=SatelReadCommand.ZONE_TEMPERATURE,
        data_type=SatelZoneTemperature,
        expected_data_lengths=(3,),
    ),
    SatelReadCommand.INTEGRA_VERSION: ReadCommandSpec(
        command=SatelReadCommand.INTEGRA_VERSION,
        data_type=SatelPanelInfo,
        expected_data_lengths=(14,),
    ),
    SatelReadCommand.READ_DEVICE_NAME: ReadCommandSpec(
        command=SatelReadCommand.READ_DEVICE_NAME,
        decoder=_decode_device_read_message,
    ),
}
