from satel_integra.utils import checksum, decode_bitmask_le, encode_bitmask_le

import pytest

test_frames = [
    # Example from 0x80 command
    ([1, 2, 29], 4, [0x03, 0x00, 0x00, 0x10]),
    # Example from 0x86 command
    (
        [1, 3, 62, 120],
        16,
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
        ],
    ),
    # Example from 0x8A command
    ([4, 63], 8, [0x10, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x80]),
]


def test_checksum_known_value() -> None:
    """Test checksum calculation against known value from manual (Appendix 4)."""
    data = bytearray([0xFE, 0xFE, 0xE0, 0x12, 0x34, 0xFF, 0xFF, 0x8A, 0x9B, 0xFE, 0x0D])

    c = checksum(data[2:-4])  # exclude headers, footers and checksum itself

    assert c == 0x8A9B


@pytest.mark.parametrize("input,length,expected", test_frames)
def test_bitmask_bytes_encoding(input, length, expected) -> None:
    """Test encode_bitmask_le function."""
    result = encode_bitmask_le(input, length)

    assert isinstance(result, bytes)
    assert result == bytearray(expected)


@pytest.mark.parametrize("expected,length,input", test_frames)
def test_bitmask_bytes_decoding(expected, length, input) -> None:
    """Test bitmask_bytes_le function."""
    result = decode_bitmask_le(bytearray(input), length)

    assert isinstance(result, list)
    assert result == expected
