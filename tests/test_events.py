from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from satel_integra.commands import SatelReadCommand
from satel_integra.const import FRAME_END, FRAME_START
from satel_integra.messages import SatelReadMessage, SatelTypedReadMessage
from satel_integra.models import (
    SatelEvent,
    SatelEventClass,
    SatelEventRecord,
    SatelEventText,
)
from satel_integra.satel_integra import MAX_EVENTS_PER_READ, AsyncSatel
from satel_integra.utils import checksum

NOW = datetime(2026, 11, 22, 18, 0)

# 0x8C answer: 8-byte record, 3-byte event index, 3-byte index used for the call.
# 2026 mod 4 = 2 (YY=10), Z=1, E=1, monitoring 11/11; class 010 (arming), day 22;
# month 11, 17:53 = 1073 min (0x431); partition 1 (PPPPP=00000), restore 0,
# code 0x001; source 5; object 0, user control number 3.
ARMING_RECORD = bytes([0b10111111, 0b01010110, 0xB4, 0x31, 0x00, 0x01, 0x05, 0x03])
ARMING_ANSWER = ARMING_RECORD + bytes([0x00, 0x12, 0x34, 0xFF, 0xFF, 0xFF])


def _raw_frame(payload: bytes) -> bytearray:
    csum = checksum(bytearray(payload))
    return (
        bytearray(FRAME_START)
        + bytearray(payload)
        + bytearray([csum >> 8, csum & 0xFF])
        + bytearray(FRAME_END)
    )


def _event_message(answer: bytes) -> SatelTypedReadMessage:
    msg = SatelReadMessage.decode_frame(_raw_frame(bytes([0x8C]) + answer))
    assert isinstance(msg, SatelTypedReadMessage)
    return msg


def _text_message(code_bytes: bytes, kind: int, text: str) -> SatelTypedReadMessage:
    payload = (
        bytes([0x8F])
        + code_bytes
        + bytes([kind, 0x00, 0x00])
        + text.encode("cp1250").ljust(46, b" ")
    )
    msg = SatelReadMessage.decode_frame(_raw_frame(payload))
    assert isinstance(msg, SatelTypedReadMessage)
    return msg


def test_event_record_decodes_protocol_fields():
    record = _event_message(ARMING_ANSWER).data

    assert isinstance(record, SatelEventRecord)
    assert record.present
    assert record.event_class is SatelEventClass.ARMING
    assert record.timestamp(NOW) == datetime(2026, 11, 22, 17, 53)
    # PPPPP as stored; whether it is a partition depends on the description kind.
    assert record.partition_field == 0
    assert not record.restore
    assert record.code == 0x001
    assert record.source == 5
    assert record.object == 0
    assert record.user_control_number == 3
    assert record.index == 0x001234


def test_event_record_year_before_reference_year():
    # Year marker 1 read in January 2027 (marker 3) is 2025, not 2029.
    answer = bytes([0b01110000]) + ARMING_ANSWER[1:]
    record = _event_message(answer).data

    assert record.timestamp(datetime(2027, 1, 2)).year == 2025


def test_event_record_invalid_date_has_no_timestamp():
    # Day 0 is not a date.
    answer = ARMING_ANSWER[:1] + bytes([0b01000000]) + ARMING_ANSWER[2:]

    assert _event_message(answer).data.timestamp(NOW) is None


def test_empty_event_record():
    answer = bytes(8) + bytes([0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF])

    assert not _event_message(answer).data.present


def test_event_text_decodes_long_description():
    text = _text_message(bytes([0x80, 0x01]), 2, "Załączenie czuwania").data

    assert isinstance(text, SatelEventText)
    assert text.kind == 2
    assert text.text == "Załączenie czuwania"


def _event_with(partition_field: int, kind: int) -> SatelEvent:
    # PPPPP sits in bits 7..3 of the fifth record byte.
    answer = ARMING_ANSWER[:4] + bytes([partition_field << 3]) + ARMING_ANSWER[5:]
    return SatelEvent(
        record=_event_message(answer).data, timestamp=None, text="", kind=kind
    )


@pytest.mark.parametrize("kind", [1, 2, 5, 11, 15, 32])
def test_event_partition_for_partition_kinds(kind):
    assert _event_with(partition_field=3, kind=kind).partition == 4


# 0: no details, 6 and 30: keypad, 7: user only, 14: telephone; on a
# one-partition panel user-function and system events carried 2 and 17 here.
@pytest.mark.parametrize("kind", [0, 6, 7, 14, 30])
def test_event_has_no_partition_for_other_kinds(kind):
    event = _event_with(partition_field=16, kind=kind)

    assert event.partition is None
    assert event.record.partition_field == 16


@pytest.fixture
def satel() -> AsyncSatel:
    client = AsyncSatel("localhost", 7094)
    client._send_data_and_wait = AsyncMock()  # type: ignore[method-assign]
    return client


@pytest.mark.asyncio
async def test_read_event_requests_newest_by_default(satel):
    satel._send_data_and_wait.side_effect = [
        _event_message(ARMING_ANSWER).data,
        _text_message(bytes([0x80, 0x01]), 2, "Załączenie czuwania").data,
    ]

    event = await satel.read_event(now=NOW)

    assert event is not None
    assert event.text == "Załączenie czuwania"
    assert event.timestamp == datetime(2026, 11, 22, 17, 53)
    event_request = satel._send_data_and_wait.await_args_list[0].args[0]
    assert event_request.cmd is SatelReadCommand.READ_EVENT
    assert event_request.msg_data == bytearray([0xFF, 0xFF, 0xFF])
    text_request = satel._send_data_and_wait.await_args_list[1].args[0]
    # Long text (bit 15), restore flag (bit 10) clear, event code 0x001.
    assert text_request.msg_data == bytearray([0x80, 0x01])


@pytest.mark.asyncio
async def test_read_event_returns_none_for_empty_record(satel):
    satel._send_data_and_wait.return_value = _event_message(
        bytes(8) + bytes([0x00, 0x00, 0x00, 0xFF, 0xFF, 0xFF])
    ).data

    assert await satel.read_event() is None


@pytest.mark.asyncio
async def test_event_texts_are_read_once_per_code(satel):
    text = _text_message(bytes([0x80, 0x01]), 2, "Załączenie czuwania").data
    record = _event_message(ARMING_ANSWER).data
    satel._send_data_and_wait.side_effect = [record, text, record]

    await satel.read_event(now=NOW)
    await satel.read_event(now=NOW)

    assert satel._send_data_and_wait.await_count == 3


@pytest.mark.asyncio
async def test_read_events_follows_the_chain_and_stops_at_known_index(satel):
    newest = ARMING_RECORD + bytes([0x00, 0x00, 0x03, 0xFF, 0xFF, 0xFF])
    older = ARMING_RECORD + bytes([0x00, 0x00, 0x02, 0x00, 0x00, 0x03])
    text = _text_message(bytes([0x80, 0x01]), 2, "Załączenie czuwania").data
    satel._send_data_and_wait.side_effect = [
        _event_message(newest).data,
        text,
        _event_message(older).data,
    ]

    events = await satel.read_events(10, stop_at_index=0x000002, now=NOW)

    assert [event.index for event in events] == [0x000003]
    requests = [
        call.args[0].msg_data for call in satel._send_data_and_wait.await_args_list
    ]
    assert requests[2] == bytearray([0x00, 0x00, 0x03])


@pytest.mark.asyncio
async def test_read_events_respects_count_and_hard_limit(satel):
    satel._send_data_and_wait.side_effect = lambda msg, _type: (
        _event_message(ARMING_ANSWER).data
        if msg.cmd is SatelReadCommand.READ_EVENT
        else _text_message(bytes([0x80, 0x01]), 2, "Załączenie czuwania").data
    )

    assert len(await satel.read_events(3, now=NOW)) == 3
    with pytest.raises(ValueError, match=str(MAX_EVENTS_PER_READ)):
        await satel.read_events(MAX_EVENTS_PER_READ + 1)
