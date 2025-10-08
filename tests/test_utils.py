from satel_integra.utils import checksum, decode_bitmask_le, encode_bitmask_le

import pytest

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
    """Test encode_bitmask_le function."""
    result = encode_bitmask_le(data, length)

    assert isinstance(result, bytes)
    assert result == expected


@pytest.mark.parametrize("expected, data, length", test_frames)
def test_bitmask_bytes_decoding(
    expected: list[int],
    data: bytearray,
    length: int,
) -> None:
    """Test bitmask_bytes_le function."""
    result = decode_bitmask_le(data, length)

    assert isinstance(result, list)
    assert result == expected
