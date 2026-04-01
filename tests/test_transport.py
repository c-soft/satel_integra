import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from satel_integra.const import FRAME_END
from satel_integra.exceptions import (
    SatelConnectFailedError,
    SatelTransportDisconnectedError,
)
from satel_integra.transport import SatelBaseTransport, SatelEncryptedTransport


@pytest.fixture
def mock_transport():
    reader = AsyncMock()
    writer = AsyncMock()
    writer.close = MagicMock()
    writer.is_closing = MagicMock()
    writer.write = MagicMock()

    transport = SatelBaseTransport("localhost", 1234)

    transport._reader = reader
    transport._writer = writer

    yield transport


@pytest.fixture
def mock_encrypted_transport():
    reader = AsyncMock()
    writer = AsyncMock()
    writer.close = MagicMock()
    writer.is_closing = MagicMock()
    writer.write = MagicMock()

    transport = SatelEncryptedTransport("localhost", 1234, "some_key")

    transport._reader = reader
    transport._writer = writer

    yield transport


@pytest.fixture
def encryption_handler():
    with patch(
        "satel_integra.transport.EncryptedCommunicationHandler", autospec=True
    ) as mock:
        yield mock


@pytest.mark.asyncio
async def test_connect_success(monkeypatch):
    reader, writer = AsyncMock(), AsyncMock()
    monkeypatch.setattr(
        asyncio, "open_connection", AsyncMock(return_value=(reader, writer))
    )

    transport = SatelBaseTransport("localhost", 1234)
    await transport.connect()
    assert transport.connected


@pytest.mark.asyncio
async def test_connect_failure(monkeypatch):
    monkeypatch.setattr(
        asyncio, "open_connection", AsyncMock(side_effect=OSError("boom"))
    )
    transport = SatelBaseTransport("localhost", 1234)

    with pytest.raises(
        SatelConnectFailedError,
        match="Unable to establish TCP connection to localhost:1234",
    ):
        await transport.connect()

    assert not transport.connected


@pytest.mark.asyncio
async def test_read_initial_data(mock_transport):
    mock_transport._reader.read.return_value = b"Busy!\r\n"

    result = await mock_transport.read_initial_data()

    assert result == b"Busy!\r\n"
    mock_transport._reader.read.assert_awaited_once_with(-1)


@pytest.mark.asyncio
async def test_read_initial_data_not_connected(caplog):
    transport = SatelBaseTransport("h", 1)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(
            SatelTransportDisconnectedError,
            match="Cannot read initial data because the transport is not connected",
        ):
            await transport.read_initial_data()

    assert "Cannot read initial data, not connected." in caplog.text


@pytest.mark.asyncio
async def test_read_frame_success(mock_transport):
    from satel_integra.const import FRAME_END

    mock_transport._read_from_transport = AsyncMock(return_value=b"data" + FRAME_END)

    frame = await mock_transport.read_frame()
    assert frame is not None
    assert frame.endswith(FRAME_END)
    mock_transport._read_from_transport.assert_awaited_once()


@pytest.mark.asyncio
async def test_read_frame_not_connected():
    transport = SatelBaseTransport("h", 1)

    with pytest.raises(
        SatelTransportDisconnectedError,
        match="Cannot read a frame because the transport is not connected",
    ):
        await transport.read_frame()


@pytest.mark.asyncio
async def test_read_frame_peer_reset_raises_disconnected(mock_transport):
    mock_transport._read_from_transport = AsyncMock(
        side_effect=ConnectionResetError("boom")
    )

    with pytest.raises(
        SatelTransportDisconnectedError,
        match="Transport connection was lost while reading a frame",
    ):
        await mock_transport.read_frame()

    assert not mock_transport.connected


@pytest.mark.asyncio
async def test_read_frame_incomplete_read_raises_disconnected(mock_transport):
    mock_transport._read_from_transport = AsyncMock(
        side_effect=asyncio.IncompleteReadError(partial=b"", expected=1)
    )

    with pytest.raises(
        SatelTransportDisconnectedError,
        match="Transport connection was lost while reading a frame",
    ):
        await mock_transport.read_frame()

    mock_transport._read_from_transport.assert_awaited_once()
    assert not mock_transport.connected


@pytest.mark.asyncio
async def test_send_frame_success(mock_transport):
    frame = b"abc"

    result = await mock_transport.send_frame(frame)
    assert result
    mock_transport._writer.write.assert_called_once_with(frame)
    mock_transport._writer.drain.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_frame_not_connected():
    transport = SatelBaseTransport("h", 1)

    with pytest.raises(
        SatelTransportDisconnectedError,
        match="Cannot write a frame because the transport is not connected",
    ):
        await transport.send_frame(b"x")


@pytest.mark.asyncio
async def test_send_frame_failure(mock_transport):
    mock_transport._writer.drain.side_effect = ConnectionResetError("fail")

    with pytest.raises(
        SatelTransportDisconnectedError,
        match="Transport connection was lost while writing a frame",
    ) as excinfo:
        await mock_transport.send_frame(b"x")

    assert isinstance(excinfo.value.__cause__, ConnectionResetError)
    assert not mock_transport.connected


@pytest.mark.asyncio
async def test_close_success(mock_transport):
    mock_transport._writer.is_closing = MagicMock(return_value=False)

    # Verify initial state
    assert mock_transport.connected

    await mock_transport.close()

    assert not mock_transport.connected
    assert mock_transport._reader is None
    assert mock_transport._writer is None


@pytest.mark.asyncio
async def test_close_already_stopped(mock_transport):
    mock_transport.stopped = True
    await mock_transport.close()  # should not raise or call anything


@pytest.mark.asyncio
async def test_read_encrypted(encryption_handler, mock_encrypted_transport):
    with patch(
        "satel_integra.transport.SatelBaseTransport.connect",
        new=AsyncMock(return_value=True),
    ):
        await mock_encrypted_transport.connect()

    encryption_handler.assert_called_once_with("some_key")

    encryption_handler_inst = encryption_handler.return_value
    decrypted_data = bytes([0x01, 0x02, 0x03])
    encryption_handler_inst.extract_data_from_pdu.return_value = (
        decrypted_data + FRAME_END + bytes([0, 0, 0, 0])  # some padding at the end
    )

    encrypted_frame_length = 0xAA
    mock_encrypted_transport._reader.read.side_effect = [
        bytes([encrypted_frame_length])
    ]
    mock_encrypted_transport._reader.readexactly.return_value = b"some_encrypted_data"

    result = await mock_encrypted_transport.read_frame()
    assert result == decrypted_data + FRAME_END
    mock_encrypted_transport._reader.readexactly.assert_awaited_with(
        encrypted_frame_length
    )
    encryption_handler_inst.extract_data_from_pdu.assert_called_with(
        b"some_encrypted_data"
    )


@pytest.mark.asyncio
async def test_read_encrypted_eof_raises_disconnected(mock_encrypted_transport):
    mock_encrypted_transport._reader.read.return_value = b""

    with pytest.raises(
        SatelTransportDisconnectedError,
        match="Transport connection was lost while reading encrypted frame length",
    ):
        await mock_encrypted_transport.read_frame()

    assert not mock_encrypted_transport.connected


@pytest.mark.asyncio
async def test_write_encrypted(encryption_handler, mock_encrypted_transport):
    encryption_handler_inst = encryption_handler.return_value
    encrypted_data = b"some_encrypted_data"
    encryption_handler_inst.prepare_pdu.return_value = encrypted_data

    with patch(
        "satel_integra.transport.SatelBaseTransport.connect",
        new=AsyncMock(return_value=True),
    ) as base_connect_mock:
        await mock_encrypted_transport.connect()

    base_connect_mock.assert_awaited_once()

    assert mock_encrypted_transport._encryption_handler is not None

    result = await mock_encrypted_transport.send_frame(b"some_plain_data")
    assert result
    encryption_handler_inst.prepare_pdu.assert_called_with(b"some_plain_data")
    mock_encrypted_transport._writer.write.assert_called_once_with(
        bytes([len(encrypted_data)]) + encrypted_data
    )
