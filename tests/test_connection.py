import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, PropertyMock
from satel_integra.connection import SatelConnection


@pytest.fixture
def mock_transport():
    transport = MagicMock()
    type(transport).closed = PropertyMock(return_value=False)
    type(transport).connected = PropertyMock(return_value=False)

    transport.connect = AsyncMock(return_value=None)
    transport.wait_connected = AsyncMock(return_value=True)
    transport.check_connection = AsyncMock(return_value=True)
    transport.close = AsyncMock()

    return transport


@pytest.fixture
def mock_connection(mock_transport: AsyncMock) -> SatelConnection:
    """Fixture that returns a SatelConnection with a patched _transport."""
    conn = SatelConnection("127.0.0.1", 7094)
    conn._transport = mock_transport
    return conn


@pytest.mark.asyncio
async def test_connect_success(mock_connection, mock_transport):
    result = await mock_connection.connect()
    assert result is True

    mock_transport.connect.assert_awaited_once()
    mock_transport.wait_connected.assert_awaited_once()
    mock_transport.check_connection.assert_awaited_once()
    mock_transport.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_config_failure(mock_connection, mock_transport):
    mock_transport.wait_connected.return_value = False

    result = await mock_connection.connect()
    assert result is False

    mock_transport.connect.assert_awaited_once()
    mock_transport.wait_connected.assert_awaited_once()
    mock_transport.check_connection.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_device_busy_failure(mock_connection, mock_transport):
    mock_transport.check_connection.return_value = False

    result = await mock_connection.connect()
    assert result is False

    mock_transport.check_connection.assert_awaited_once()
    mock_transport.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_skips_busy_check_when_disabled(mock_connection, mock_transport):
    mock_transport.check_connection.return_value = False

    result = await mock_connection.connect(check_busy=False)
    assert result is True

    mock_transport.connect.assert_awaited_once()
    mock_transport.wait_connected.assert_awaited_once()
    mock_transport.check_connection.assert_not_awaited()
    mock_transport.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_skipped_when_closed(mock_connection, mock_transport):
    mock_connection._closed = True

    result = await mock_connection.connect()
    assert result is False

    mock_transport.connect.assert_not_awaited()
    mock_transport.check_connection.assert_not_awaited()
    mock_transport.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_connected_already_connected(mock_connection, mock_transport):
    type(mock_transport).connected = PropertyMock(return_value=True)

    result = await mock_connection.ensure_connected()
    assert result is True

    mock_transport.connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_connected_reconnect(mock_connection, mock_transport):
    # Simulate disconnected state first, then connected after retry
    type(mock_transport).connected = PropertyMock(
        side_effect=[False, False, False, True, True]
    )

    result = await mock_connection.ensure_connected()

    assert result is True
    assert mock_transport.connect.await_count >= 1


@pytest.mark.asyncio
async def test_close_success(mock_connection, mock_transport):
    type(mock_transport).connected = PropertyMock(return_value=True)

    assert mock_transport.closed is False
    assert mock_transport.connected is True

    await mock_connection.close()

    mock_transport.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_already_closed(mock_connection, mock_transport):
    mock_connection._closed = True

    await mock_connection.close()  # should not raise or call anything

    mock_transport.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconnection_event_set_on_subsequent_connect(
    mock_connection, mock_transport
):
    """First successful connect should set `_had_connection` but not the event.

    A subsequent successful connect should set the `_reconnected_event` so
    waiters are notified.
    """
    # First connect (initial connection)
    result = await mock_connection.connect()
    assert result is True

    # After initial connect, we have had a connection but the reconnection
    # event should not be set.
    assert mock_connection._had_connection is True
    assert mock_connection._reconnected_event.is_set() is False

    # Ensure the event is cleared, then call connect() again to simulate a
    # subsequent reconnection — the event should be set this time.
    mock_connection._reconnected_event.clear()

    # Simulate disconnected state at start of second connect
    type(mock_transport).connected = PropertyMock(return_value=False)

    await mock_connection.connect()

    assert mock_connection._reconnected_event.is_set() is True


@pytest.mark.asyncio
async def test_wait_reconnected_blocks_and_returns_true(
    mock_connection, mock_transport
):
    """`wait_reconnected()` should block until `_reconnected_event` is set
    and then return True (when not closed).
    """
    # Ensure we've had an initial connection so wait_reconnected will wait for
    # a later reconnection.
    await mock_connection.connect()

    waiter = asyncio.create_task(mock_connection.wait_reconnected())

    # Give the loop a tick so the waiter can clear the event and start waiting
    await asyncio.sleep(0)

    # Now signal reconnection and await the waiter result
    mock_connection._reconnected_event.set()

    result = await asyncio.wait_for(waiter, timeout=1.0)
    assert result is True
