import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from satel_integra.connection import SatelConnection


@pytest.mark.asyncio
async def test_connect_success(monkeypatch):
    reader, writer = AsyncMock(), AsyncMock()
    monkeypatch.setattr(
        asyncio, "open_connection", AsyncMock(return_value=(reader, writer))
    )

    conn = SatelConnection("localhost", 1234)
    assert await conn.connect() is True
    assert conn.connected


@pytest.mark.asyncio
async def test_connect_failure(monkeypatch):
    monkeypatch.setattr(
        asyncio, "open_connection", AsyncMock(side_effect=OSError("boom"))
    )
    conn = SatelConnection("localhost", 1234)
    assert await conn.connect() is False
    assert not conn.connected


@pytest.mark.asyncio
async def test_connect_skipped_when_closed():
    conn = SatelConnection("localhost", 1234)
    conn.closed = True
    assert await conn.connect() is False


@pytest.mark.asyncio
async def test_send_frame_success(monkeypatch):
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()

    conn = SatelConnection("host", 1)
    conn._writer = writer
    frame = b"abc"
    result = await conn.send_frame(frame)
    assert result
    writer.write.assert_called_once_with(frame)
    writer.drain.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_frame_not_connected():
    conn = SatelConnection("h", 1)
    result = await conn.send_frame(b"x")
    assert result is False


@pytest.mark.asyncio
async def test_send_frame_failure(monkeypatch):
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain.side_effect = Exception("fail")
    conn = SatelConnection("h", 1)
    conn._writer = writer
    result = await conn.send_frame(b"x")
    assert not result
    assert conn._writer is None


@pytest.mark.asyncio
async def test_read_frame_success(monkeypatch):
    from satel_integra.const import FRAME_END

    reader = AsyncMock()
    reader.readuntil.return_value = b"data" + FRAME_END
    conn = SatelConnection("h", 1)
    conn._reader = reader
    frame = await conn.read_frame()
    assert frame is not None
    assert frame.endswith(FRAME_END)
    reader.readuntil.assert_awaited_once()


@pytest.mark.asyncio
async def test_read_frame_not_connected():
    conn = SatelConnection("h", 1)
    result = await conn.read_frame()
    assert result is None


@pytest.mark.asyncio
async def test_read_frame_failure(monkeypatch):
    reader = AsyncMock()
    reader.readuntil.side_effect = Exception("boom")
    conn = SatelConnection("h", 1)
    conn._reader = reader
    conn._writer = AsyncMock()
    result = await conn.read_frame()
    assert result is None
    assert conn._reader is None
    assert conn._writer is None


@pytest.mark.asyncio
async def test_read_frame_timeout():
    conn = SatelConnection("host", 1234)
    conn._reader = AsyncMock()
    conn._reader.readuntil.side_effect = asyncio.TimeoutError()

    result = await conn.read_frame()
    assert result is None
    assert not conn.connected  # Should disconnect on timeout


@pytest.mark.asyncio
async def test_ensure_connected_already_connected():
    conn = SatelConnection("h", 1)
    conn._reader = AsyncMock()
    conn._writer = AsyncMock()
    assert await conn.ensure_connected() is True


@pytest.mark.asyncio
async def test_ensure_connected_reconnect(monkeypatch):
    conn = SatelConnection("h", 1, reconnection_timeout=0)
    calls = 0

    async def fake_connect():
        nonlocal calls
        calls += 1
        if calls < 2:
            return False
        conn._reader, conn._writer = AsyncMock(), AsyncMock()
        return True

    conn.connect = fake_connect
    result = await conn.ensure_connected()
    assert result
    assert calls == 2


@pytest.mark.asyncio
async def test_close_success(monkeypatch):
    writer = MagicMock()
    writer.is_closing.return_value = False
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()

    conn = SatelConnection("h", 1)
    conn._reader, conn._writer = AsyncMock(), writer

    # Verify initial state
    assert not conn.closed
    assert conn.connected

    await conn.close()

    assert conn.closed
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()

    assert conn._reader is None
    assert conn._writer is None


@pytest.mark.asyncio
async def test_close_already_closed():
    conn = SatelConnection("h", 1)
    conn.closed = True
    await conn.close()  # should not raise or call anything
