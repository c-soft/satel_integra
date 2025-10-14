"""Constants for the Satel Integra integration."""

FRAME_START = bytes([0xFE, 0xFE])
FRAME_END = bytes([0xFE, 0x0D])

FRAME_SPECIAL_BYTES = bytes([0xFE])
FRAME_SPECIAL_BYTES_REPLACEMENT = bytes([0xFE, 0xF0])

MESSAGE_RESPONSE_TIMEOUT = 5
