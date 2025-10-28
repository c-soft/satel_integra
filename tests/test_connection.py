import pytest
from unittest.mock import AsyncMock, MagicMock, PropertyMock
from satel_integra.connection import SatelConnection


@pytest.fixture
def mock_transport():
    transport = MagicMock()
    type(transport).closed = PropertyMock(return_value=False)
    type(transport).connected = PropertyMock(return_value=True)

    transport.connect = AsyncMock(return_value=True)
    transport.check_connection = AsyncMock(return_value=True)
    transport.close = AsyncMock()

    return transport


@pytest.fixture
def mock_connection(mock_transport: AsyncMock) -> SatelConnection:
    """Fixture that returns a SatelConnection with a patched _connection."""
    conn = SatelConnection("1270.0.1", 7094)
    conn._connection = mock_transport
    return conn


@pytest.mark.asyncio
async def test_connect_success(mock_connection, mock_transport):
    result = await mock_connection.connect()
    assert result is True

    mock_transport.connect.assert_awaited_once()
    mock_transport.check_connection.assert_awaited_once()
    mock_transport.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_config_failure(mock_connection, mock_transport):
    mock_transport.connect.return_value = False

    result = await mock_connection.connect()
    assert result is False

    mock_transport.connect.assert_awaited_once()
    mock_transport.check_connection.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_device_busy_failure(mock_connection, mock_transport):
    mock_transport.check_connection.return_value = False

    result = await mock_connection.connect()
    assert result is False

    mock_transport.check_connection.assert_awaited_once()
    mock_transport.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_skipped_when_closed(mock_connection, mock_transport):
    type(mock_transport).closed = PropertyMock(return_value=True)

    result = await mock_connection.connect()
    assert result is False

    mock_transport.connect.assert_not_awaited()
    mock_transport.check_connection.assert_not_awaited()
    mock_transport.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_connected_already_connected(mock_connection, mock_transport):
    type(mock_transport).connected = True

    result = await mock_connection.ensure_connected()
    assert result is True

    mock_transport.connect.assert_not_awaited


@pytest.mark.asyncio
async def test_ensure_connected_reconnect(mock_connection, mock_transport):
    type(mock_transport).connected = PropertyMock(
        side_effect=[False, False, False, True, True]
    )

    result = await mock_connection.ensure_connected()

    assert result is True
    assert mock_transport.connect.await_count == 2


@pytest.mark.asyncio
async def test_close_success(mock_connection, mock_transport):
    assert mock_transport.closed is False
    assert mock_transport.connected is True

    await mock_connection.close()

    mock_transport.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_already_closed(mock_connection, mock_transport):
    type(mock_transport).closed = True

    await mock_connection.close()  # should not raise or call anything
