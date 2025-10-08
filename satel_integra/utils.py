"""Utility functions for Satel Integra integration."""


def checksum(command):
    """Function to calculate checksum as per Satel manual."""
    crc = 0x147A
    for b in command:
        # rotate (crc 1 bit left)
        crc = ((crc << 1) & 0xFFFF) | (crc & 0x8000) >> 15
        crc = crc ^ 0xFFFF
        crc = (crc + (crc >> 8) + b) & 0xFFFF
    return crc


def encode_bitmask_le(indices: list[int], length: int) -> bytes:
    """Convert a list of bit positions to a fixed-length little-endian byte array.

    Used for partitions, zones, outputs, expanders, etc.
    """
    ret_val = 0
    for pos in indices:
        if pos > length * 8:
            msg = f"Position {pos} out of bounds for length {length}"
            raise IndexError(msg)
        ret_val |= 1 << (pos - 1)
    return ret_val.to_bytes(length, "little")


def decode_bitmask_le(data: bytes, expected_length: int) -> list[int]:
    """Return list of positions of bits set to one in given data.

    This method is used to read e.g. violated zones. They are marked by ones
    on respective bit positions - as per Satel manual.
    """
    if len(data) != expected_length:
        msg = (
            f"Invalid bitmask length: expected {expected_length} bytes, got {len(data)}"
        )
        raise ValueError(msg)

    set_bit_numbers = []
    bit_index = 1

    for byte in data[0:]:
        for i in range(8):
            if byte & (1 << i):
                set_bit_numbers.append(bit_index)
            bit_index += 1

    return set_bit_numbers
