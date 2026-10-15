"""Constants for the Satel Integra integration."""

from collections.abc import Awaitable, Callable

FRAME_START = bytes([0xFE, 0xFE])
FRAME_END = bytes([0xFE, 0x0D])

FRAME_SPECIAL_BYTES = bytes([0xFE])
FRAME_SPECIAL_BYTES_REPLACEMENT = bytes([0xFE, 0xF0])

TCP_CONNECT_TIMEOUT = 10

MESSAGE_RESPONSE_TIMEOUT = 5
# Time to collect commands issued together (e.g. a group of outputs) into one frame
COMMAND_GROUPING_DELAY = 0.05
KEEPALIVE_INTERVAL = 15
# Re-send the monitoring request this often on an unchanged connection. Per the
# protocol the module then re-sends the state of all monitored commands.
MONITORING_REFRESH_INTERVAL = 300

StateChangeCallback = Callable[[], None | Awaitable[None]]
UnsubscribeCallback = Callable[[], None]

# A queued control command (arm, disarm, outputs) older than this is not sent.
CONTROL_COMMAND_TTL = 3
