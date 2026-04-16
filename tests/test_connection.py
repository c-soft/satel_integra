import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from satel_integra.connection import SatelConnection
from satel_integra.exceptions import (
    SatelConnectFailedError,
    SatelConnectionInitializationError,
    SatelConnectionStoppedError,
    SatelPanelBusyError,
)
from satel_integra.transport import SatelEncryptedTransport


@pytest.fixture
def mock_transport():
    transport = MagicMock()
    transport.connected = False

    async def connect():
        transport.connected = True
        return True

    async def close():
        transport.connected = False

    transport.connect = AsyncMock(side_effect=connect)
    transport.read_initial_data = AsyncMock(return_value=b"")
    transport.send_frame = AsyncMock(return_value=True)
    transport.read_frame = AsyncMock(return_value=b"probe-response")
    transport.close = AsyncMock(side_effect=close)

    return transport


@pytest.fixture
def mock_connection(mock_transport: AsyncMock) -> SatelConnection:
    """Fixture that returns a SatelConnection with a patched _transport."""
    conn = SatelConnection("127.0.0.1", 7094)
    conn._transport = mock_transport
    return conn


@pytest.mark.asyncio
async def test_connect_success(mock_connection, mock_transport):
    await mock_connection.connect()

    mock_transport.connect.assert_awaited_once()
    mock_transport.read_initial_data.assert_awaited_once()
    mock_transport.send_frame.assert_awaited_once()
    mock_transport.read_frame.assert_awaited_once()
    mock_transport.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_config_failure_raises(mock_connection, mock_transport):
    mock_transport.connect.side_effect = SatelConnectFailedError("boom")

    with pytest.raises(SatelConnectFailedError, match="boom"):
        await mock_connection.connect()

    assert mock_connection.stopped is False
    mock_transport.connect.assert_awaited_once()
    mock_transport.close.assert_awaited_once()
    mock_transport.send_frame.assert_not_awaited()
    mock_transport.read_frame.assert_not_awaited()
    mock_transport.read_initial_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_device_busy_raises(mock_connection, mock_transport):
    mock_transport.read_initial_data.return_value = b"Busy!\r\n"

    with pytest.raises(
        SatelPanelBusyError,
        match="Panel reports busy because another client is connected",
    ):
        await mock_connection.connect()

    assert mock_connection.stopped is False
    mock_transport.connect.assert_awaited_once()
    mock_transport.close.assert_awaited_once()
    mock_transport.send_frame.assert_not_awaited()
    mock_transport.read_frame.assert_not_awaited()
    mock_transport.read_initial_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_readiness_failure_closes_connection(
    mock_connection, mock_transport
):
    mock_transport.read_initial_data.return_value = None

    with pytest.raises(
        SatelConnectionInitializationError,
        match="Panel did not provide initial data after connecting",
    ):
        await mock_connection.connect()

    assert mock_connection.stopped is False
    mock_transport.connect.assert_awaited_once()
    mock_transport.close.assert_awaited_once()
    mock_transport.send_frame.assert_not_awaited()
    mock_transport.read_frame.assert_not_awaited()
    mock_transport.read_initial_data.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_can_skip_startup_verification(mock_connection, mock_transport):
    await mock_connection.connect(verify_connection=False)

    mock_transport.connect.assert_awaited_once()
    mock_transport.close.assert_not_awaited()
    mock_transport.read_initial_data.assert_not_awaited()
    mock_transport.send_frame.assert_not_awaited()
    mock_transport.read_frame.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_protocol_probe_failure_raises(mock_connection, mock_transport):
    mock_transport.read_frame.return_value = None

    with pytest.raises(
        SatelConnectionInitializationError,
        match="Panel did not respond to the startup protocol probe",
    ):
        await mock_connection.connect()

    assert mock_connection.stopped is False
    mock_transport.send_frame.assert_awaited_once()
    mock_transport.read_frame.assert_awaited_once()
    mock_transport.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_protocol_probe_timeout_raises(mock_connection, mock_transport):
    mock_transport.read_frame.side_effect = asyncio.TimeoutError

    with pytest.raises(
        SatelConnectionInitializationError,
        match="Panel did not respond to the startup protocol probe before timeout",
    ):
        await mock_connection.connect()

    assert mock_connection.stopped is False
    mock_transport.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_raises_when_stopped(mock_connection, mock_transport):
    mock_connection._stopped = True

    with pytest.raises(SatelConnectionStoppedError, match="Connection is stopped"):
        await mock_connection.connect()

    mock_transport.connect.assert_not_awaited()
    mock_transport.read_initial_data.assert_not_awaited()
    mock_transport.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_connected_already_connected(mock_connection, mock_transport):
    mock_transport.connected = True

    await mock_connection.ensure_connected()

    mock_transport.connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_connected_reconnect(mock_connection, mock_transport):
    await mock_connection.ensure_connected()
    assert mock_transport.connect.await_count == 1


@pytest.mark.asyncio
async def test_ensure_connected_retries_after_transient_connect_failure(
    mock_connection, mock_transport, monkeypatch
):
    attempts = 0

    async def flaky_connect():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            mock_transport.connected = False
            raise SatelConnectFailedError("boom")

        mock_transport.connected = True
        return True

    mock_transport.connect = AsyncMock(side_effect=flaky_connect)
    sleep_mock = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)

    await asyncio.wait_for(mock_connection.ensure_connected(), timeout=1.0)

    assert attempts == 2
    assert mock_connection.stopped is False
    sleep_mock.assert_awaited_once_with(mock_connection._reconnection_timeout)


@pytest.mark.asyncio
async def test_ensure_connected_raises_when_stopped(mock_connection):
    mock_connection._stopped = True

    with pytest.raises(SatelConnectionStoppedError):
        await mock_connection.ensure_connected()


@pytest.mark.asyncio
async def test_disconnect_closes_transport_without_stopping_client(
    mock_connection, mock_transport
):
    mock_transport.connected = True

    await mock_connection.disconnect()

    assert mock_connection.stopped is False
    mock_transport.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_connection_read_exception(mock_connection, mock_transport):
    mock_transport.connected = True
    mock_transport.read_initial_data.side_effect = Exception("boom")

    with pytest.raises(
        SatelConnectionInitializationError,
        match="Panel failed connection readiness checks",
    ):
        await mock_connection._check_connection()

    assert mock_connection.stopped is False
    mock_transport.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_connection_raises_when_initial_read_unavailable(
    mock_connection, mock_transport
):
    mock_transport.connected = True
    mock_transport.read_initial_data.return_value = None

    with pytest.raises(
        SatelConnectionInitializationError,
        match="Panel did not provide initial data after connecting",
    ):
        await mock_connection._check_connection()

    mock_transport.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_encrypted_check_connection_treats_unexpected_data_as_busy(
    mock_connection, mock_transport
):
    mock_transport.read_initial_data.return_value = b"\x93\xaa\x10\x01"
    mock_connection._transport = SatelEncryptedTransport(
        "127.0.0.1", 7094, "abcdefghijkl"
    )
    mock_connection._transport.read_initial_data = mock_transport.read_initial_data
    mock_connection._transport.close = mock_transport.close
    mock_connection._transport._reader = object()
    mock_connection._transport._writer = object()

    with pytest.raises(
        SatelPanelBusyError,
        match="Encrypted panel returned startup data indicating the session is busy",
    ):
        await mock_connection._check_connection()

    mock_transport.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_encrypted_check_connection_timeout_is_healthy(
    mock_connection, mock_transport
):
    async def long_read():
        await asyncio.sleep(999)
        return b""

    mock_connection._transport = SatelEncryptedTransport(
        "127.0.0.1", 7094, "abcdefghijkl"
    )
    mock_connection._transport.read_initial_data = AsyncMock(side_effect=long_read)
    mock_connection._transport.close = mock_transport.close
    mock_connection._transport._reader = object()
    mock_connection._transport._writer = object()

    result = await mock_connection._check_connection()

    assert result is None
    mock_transport.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_success(mock_connection, mock_transport):
    mock_transport.connected = True

    await mock_connection.close()

    mock_transport.close.assert_awaited_once()
    assert mock_connection.stopped is True


@pytest.mark.asyncio
async def test_close_already_stopped(mock_connection, mock_transport):
    mock_connection._stopped = True

    await mock_connection.close()

    mock_transport.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconnection_event_set_on_subsequent_connect(
    mock_connection, mock_transport
):
    await mock_connection.connect()

    assert mock_connection._had_connection is True
    assert mock_connection._reconnected_event.is_set() is False

    mock_connection._reconnected_event.clear()
    mock_transport.connected = False

    await mock_connection.connect()

    assert mock_connection._reconnected_event.is_set() is True


@pytest.mark.asyncio
async def test_wait_reconnected_blocks_and_returns_true(
    mock_connection, mock_transport
):
    await mock_connection.connect()

    waiter = asyncio.create_task(mock_connection.wait_reconnected())
    await asyncio.sleep(0)
    mock_connection._reconnected_event.set()

    await asyncio.wait_for(waiter, timeout=1.0)


@pytest.mark.asyncio
async def test_wait_reconnected_raises_when_connection_closes(
    mock_connection, mock_transport
):
    await mock_connection.connect()

    waiter = asyncio.create_task(mock_connection.wait_reconnected())

    await asyncio.sleep(0)
    await mock_connection.close()

    with pytest.raises(SatelConnectionStoppedError):
        await asyncio.wait_for(waiter, timeout=1.0)


@pytest.mark.asyncio
async def test_wait_stopped_blocks_until_connection_closes(
    mock_connection, mock_transport
):
    waiter = asyncio.create_task(mock_connection.wait_stopped())

    await asyncio.sleep(0)
    await mock_connection.close()

    await asyncio.wait_for(waiter, timeout=1.0)
