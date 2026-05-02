import pytest

from satel_integra.utils import (
    checksum,
    decode_bitmask_le,
    decode_device_number,
    decode_temperature,
    encode_bitmask_le,
    encode_device_number,
)

# List values, byte data, length
test_frames: list[tuple[list[int], bytearray, int]] = [
    # Example from 0x00 command
    (
        [3, 14, 128],
        bytearray(
            [
                0x04,
                0x20,
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,
                0x80,
            ]
        ),
        16,
    ),
    # Example from 0x80 command
    ([1, 2, 29], bytearray([0x03, 0x00, 0x00, 0x10]), 4),
    # Example from 0x86 command
    (
        [1, 3, 62, 120],
        bytearray(
            [
                0x05,
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,
                0x20,
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,
                0x00,
                0x80,
                0x00,
            ]
        ),
        16,
    ),
]


def test_checksum_known_value() -> None:
    """Test checksum calculation against known value from manual (Appendix 4)."""
    data = bytearray([0xFE, 0xFE, 0xE0, 0x12, 0x34, 0xFF, 0xFF, 0x8A, 0x9B, 0xFE, 0x0D])

    c = checksum(data[2:-4])  # exclude headers, footers and checksum itself

    assert c == 0x8A9B


@pytest.mark.parametrize("data, expected, length", test_frames)
def test_bitmask_bytes_encoding(
    data: list[int],
    expected: bytearray,
    length: int,
) -> None:
    result = encode_bitmask_le(data, length)

    assert isinstance(result, bytes)
    assert result == expected


@pytest.mark.parametrize("expected, data, length", test_frames)
def test_bitmask_bytes_decoding(
    expected: list[int],
    data: bytearray,
    length: int,
) -> None:
    result = decode_bitmask_le(data, length)

    assert isinstance(result, list)
    assert result == expected


@pytest.mark.parametrize(
    "device_number, expected",
    [
        (42, 42),
        (256, 0),
    ],
)
def test_encode_device_number(device_number: int, expected: int) -> None:
    assert encode_device_number(device_number) == expected


@pytest.mark.parametrize("device_number", [0, 257])
def test_encode_device_number_validates_range(device_number: int) -> None:
    with pytest.raises(ValueError, match="device_number must be between 1 and 256"):
        encode_device_number(device_number)


@pytest.mark.parametrize(
    "encoded_device_number, expected",
    [
        (42, 42),
        (0, 256),
    ],
)
def test_decode_device_number(encoded_device_number: int, expected: int) -> None:
    assert decode_device_number(encoded_device_number) == expected


@pytest.mark.parametrize("encoded_device_number", [-1, 256])
def test_decode_device_number_validates_encoded_range(
    encoded_device_number: int,
) -> None:
    with pytest.raises(
        ValueError, match="encoded device_number must be between 0 and 255"
    ):
        decode_device_number(encoded_device_number)


@pytest.mark.parametrize(
    "high, low, expected",
    [
        (0x00, 0x00, -55.0),
        (0x00, 0x01, -54.5),
        (0x00, 0x6E, 0.0),
        (0x00, 0x96, 20.0),
        (0xFF, 0xFF, None),
    ],
)
def test_decode_temperature(high: int, low: int, expected: float | None) -> None:
    assert decode_temperature(high, low) == expected
