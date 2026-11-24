"""Event log records read from the panel."""

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum, unique

# Event texts are in the panel's language; Polish panels use Windows-1250.
EVENT_TEXT_ENCODING = "cp1250"

# Description kinds (0x8F) whose PPPPP field is a partition. In other kinds the
# same bits are e.g. a keypad (6, 30) or a telephone (14), or carry nothing (0).
PARTITION_KINDS = frozenset({1, 2, 5, 11, 15, 32})


@unique
class SatelEventClass(IntEnum):
    """Event classes (KKK bits of the 0x8C event record)."""

    ZONE_ALARMS = 0
    PARTITION_ALARMS = 1
    ARMING = 2
    BYPASSES = 3
    ACCESS_CONTROL = 4
    TROUBLES = 5
    USER_FUNCTIONS = 6
    SYSTEM_EVENTS = 7


@dataclass(frozen=True)
class SatelEventRecord:
    """One record of the panel event log (0x8C answer)."""

    present: bool
    year_marker: int
    event_class: SatelEventClass
    day: int
    month: int
    minutes: int
    partition_field: int
    restore: bool
    code: int
    source: int
    object: int
    user_control_number: int
    index: int

    @classmethod
    def _from_payload(cls, payload: bytes) -> "SatelEventRecord":
        """Parse the 14-byte answer: record, its index, the index used to call."""
        return cls(
            # Z (record not empty) and E (event present) are both set for an event.
            present=bool(payload[0] & 0x20) and bool(payload[0] & 0x10),
            year_marker=payload[0] >> 6,
            event_class=SatelEventClass(payload[1] >> 5),
            day=payload[1] & 0x1F,
            month=payload[2] >> 4,
            minutes=((payload[2] & 0x0F) << 8) | payload[3],
            # PPPPP as stored (0-based); its meaning depends on the description
            # kind, see SatelEvent.partition.
            partition_field=payload[4] >> 3,
            restore=bool(payload[4] & 0x04),
            code=((payload[4] & 0x03) << 8) | payload[5],
            source=payload[6],
            object=payload[7] >> 5,
            user_control_number=payload[7] & 0x1F,
            index=int.from_bytes(payload[8:11]),
        )

    def timestamp(self, reference: datetime) -> datetime | None:
        """Return the event time, or None if invalid; the record keeps the year mod 4."""
        year = reference.year - reference.year % 4 + self.year_marker
        if year > reference.year:
            year -= 4
        try:
            return datetime(
                year, self.month, self.day, self.minutes // 60, self.minutes % 60
            )
        except ValueError:
            return None


@dataclass(frozen=True)
class SatelEventText:
    """Event description from the panel (0x8F answer)."""

    kind: int
    text: str

    @classmethod
    def _from_payload(cls, payload: bytes) -> "SatelEventText":
        """Parse the long (51-byte) or short (21-byte) description answer."""
        long_text = bool(payload[0] & 0x80)
        return cls(
            kind=payload[2] if long_text else int.from_bytes(payload[3:5]),
            text=payload[5:].decode(EVENT_TEXT_ENCODING).rstrip("\x00 ").strip(),
        )


@dataclass(frozen=True)
class SatelEvent:
    """An event log record with its time and description."""

    record: SatelEventRecord
    timestamp: datetime | None
    text: str
    kind: int

    @property
    def partition(self) -> int | None:
        """Partition number (1-based), or None if this kind of event has none."""
        if self.kind not in PARTITION_KINDS:
            return None
        return self.record.partition_field + 1

    @property
    def index(self) -> int:
        """Index of this event; read it to get the event before."""
        return self.record.index
