"""Utility functions for Satel Integra integration."""


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
