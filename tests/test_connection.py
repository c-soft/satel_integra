import asyncio
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from satel_integra.connection import SatelConnection
from satel_integra.const import FRAME_END


@pytest.fixture
def reader_writer(monkeypatch):
    reader = AsyncMock()
    writer = AsyncMock()
    monkeypatch.setattr(
        asyncio, "open_connection", AsyncMock(return_value=(reader, writer))
    )
    yield reader, writer


@pytest.fixture
def encryption_handler():
    with patch(
        "satel_integra.connection.EncryptedCommunicationHandler", autospec=True
    ) as mock:
        yield mock


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

    assert conn._writer is None
    assert conn._reader is None


@pytest.mark.asyncio
async def test_connect_busy_message(monkeypatch, caplog):
    reader, writer = AsyncMock(), MagicMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()

    reader.read.return_value = b"\x10Busy!\r\n\xd8\xa5\xa5\xa5\xa5\xa5\xa5\xa5"

    monkeypatch.setattr(
        asyncio, "open_connection", AsyncMock(return_value=(reader, writer))
    )

    conn = SatelConnection("localhost", 1234)

    with caplog.at_level(logging.WARNING):
        assert await conn.connect() is False

    assert "Panel reports busy, another client is connected." in caplog.text
    assert not conn.connected

    assert conn._writer is None
    assert conn._reader is None


@pytest.mark.asyncio
async def test_connect_read_exception(monkeypatch, caplog):
    reader, writer = AsyncMock(), MagicMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    reader.read = AsyncMock(side_effect=Exception("Test exception"))

    monkeypatch.setattr(
        asyncio, "open_connection", AsyncMock(return_value=(reader, writer))
    )

    conn = SatelConnection("localhost", 1234)

    with caplog.at_level(logging.WARNING):
        assert await conn.connect() is False

    assert "Connection check failed: Test exception" in caplog.text
    assert not conn.connected

    assert conn._writer is None
    assert conn._reader is None


@pytest.mark.asyncio
async def test_connect_read_timeout(monkeypatch, caplog):
    async def long_read(length):
        await asyncio.sleep(999)
        return ""

    reader, writer = AsyncMock(), MagicMock()
    reader.read = AsyncMock(side_effect=long_read)
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()

    monkeypatch.setattr(
        asyncio, "open_connection", AsyncMock(return_value=(reader, writer))
    )

    conn = SatelConnection("localhost", 1234)

    assert await conn.connect() is True

    assert conn.connected

    assert conn._writer is writer
    assert conn._reader is reader


@pytest.mark.asyncio
async def test_connect_skipped_when_closed():
    conn = SatelConnection("localhost", 1234)
    conn.closed = True
    assert await conn.connect() is False


@pytest.mark.asyncio
async def test_send_frame_success(reader_writer):
    _, writer = reader_writer

    conn = SatelConnection("host", 1)
    await conn.connect()
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
async def test_send_frame_failure(reader_writer):
    _, writer = reader_writer

    writer.drain.side_effect = Exception("fail")
    conn = SatelConnection("h", 1)
    await conn.connect()
    assert conn.connected
    result = await conn.send_frame(b"x")
    assert not result
    assert not conn.connected


@pytest.mark.asyncio
async def test_read_frame_success(reader_writer):
    reader, _ = reader_writer
    from satel_integra.const import FRAME_END

    reader.readuntil.return_value = b"data" + FRAME_END
    conn = SatelConnection("h", 1)
    await conn.connect()
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
async def test_read_frame_failure(reader_writer):
    reader, _ = reader_writer
    reader.readuntil.side_effect = Exception("boom")
    conn = SatelConnection("h", 1)
    await conn.connect()
    assert conn.connected
    result = await conn.read_frame()
    assert result is None
    assert not conn.connected


@pytest.mark.asyncio
async def test_read_frame_timeout(reader_writer):
    reader, _ = reader_writer
    reader.readuntil.side_effect = asyncio.TimeoutError()
    conn = SatelConnection("host", 1234)
    await conn.connect()

    result = await conn.read_frame()
    assert result is None
    reader.readuntil.assert_awaited_once()
    assert not conn.connected  # Should disconnect on timeout


@pytest.mark.asyncio
async def test_ensure_connected_already_connected(reader_writer):
    conn = SatelConnection("h", 1)
    await conn.connect()
    conn.connect = AsyncMock()
    assert await conn.ensure_connected() is True
    conn.connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_connected_reconnect(monkeypatch):
    mock_connected = PropertyMock(side_effect=[False, False, False, True, True])
    monkeypatch.setattr(SatelConnection, "connected", mock_connected)
    mock_connect = AsyncMock()
    monkeypatch.setattr(SatelConnection, "connect", mock_connect)
    conn = SatelConnection("h", 1, reconnection_timeout=0)

    result = await conn.ensure_connected()
    assert result
    assert mock_connect.await_count == 2
    assert mock_connected.call_count == 5


@pytest.mark.asyncio
async def test_close_success(reader_writer):
    _, writer = reader_writer
    writer.is_closing = MagicMock(return_value=False)

    conn = SatelConnection("h", 1)
    await conn.connect()

    # Verify initial state
    assert not conn.closed
    assert conn.connected

    await conn.close()

    assert conn.closed
    writer.close.assert_called_once()
    writer.wait_closed.assert_awaited_once()

    assert not conn.connected


@pytest.mark.asyncio
async def test_close_already_closed():
    conn = SatelConnection("h", 1)
    conn.closed = True
    await conn.close()  # should not raise or call anything


@pytest.mark.asyncio
async def test_read_encrypted(reader_writer, encryption_handler):
    reader, _ = reader_writer
    conn = SatelConnection("h", 1, integration_key="some_key")
    await conn.connect()

    encryption_handler.assert_called_once_with("some_key")

    encryption_handler_inst = encryption_handler.return_value
    decrypted_data = bytes([0x01, 0x02, 0x03])
    encryption_handler_inst.extract_data_from_pdu.return_value = (
        decrypted_data + FRAME_END + bytes([0, 0, 0, 0])  # some padding at the end
    )

    encrypted_frame_length = 0xAA
    reader.read.side_effect = [
        bytes([encrypted_frame_length]),
        b"some_encrypted_data",
    ]

    result = await conn.read_frame()
    assert result == decrypted_data + FRAME_END
    reader.read.assert_awaited_with(encrypted_frame_length)
    encryption_handler_inst.extract_data_from_pdu.assert_called_with(
        b"some_encrypted_data"
    )


@pytest.mark.asyncio
async def test_read_encrypted_no_frame_end(reader_writer, encryption_handler):
    reader, _ = reader_writer
    conn = SatelConnection("h", 1, integration_key="some_key")
    await conn.connect()

    encryption_handler_inst = encryption_handler.return_value
    decrypted_data_without_frame_end = bytes([0x01, 0x02, 0x03])
    encryption_handler_inst.extract_data_from_pdu.return_value = (
        decrypted_data_without_frame_end
    )

    encrypted_frame_length = 0xAA
    reader.read.side_effect = [
        bytes([encrypted_frame_length]),
        b"some_encrypted_data",
    ]

    result = await conn.read_frame()
    assert result is None


@pytest.mark.asyncio
async def test_write_encrypted(reader_writer, encryption_handler):
    _, writer = reader_writer
    conn = SatelConnection("h", 1, integration_key="some_key")
    await conn.connect()

    encryption_handler_inst = encryption_handler.return_value
    encrypted_data = b"some_encrypted_data"
    encryption_handler_inst.prepare_pdu.return_value = encrypted_data

    result = await conn.send_frame(b"some_plain_data")
    assert result
    encryption_handler_inst.prepare_pdu.assert_called_with(b"some_plain_data")
    writer.write.assert_called_once_with(bytes([len(encrypted_data)]) + encrypted_data)
