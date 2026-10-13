"""satel-integra library against the ETHM simulator - connection fault scenarios.

Library under test: environment variable SATEL_LIB (directory containing the package) and SATEL_PACKAGE
(package name, default `satel_integra`). Without SATEL_LIB the installed package is used. On stock 1.5.0
the tests marked as BUG are EXPECTED to fail (proof of the bug); on the patched library they must pass.
Tests marked as REGRESSION pass on both.

    SATEL_LIB=/path/to/library python -m pytest tests -q
"""

import asyncio
import os
import pathlib
import sys

import pytest

SIMULATOR_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SIMULATOR_DIR))
if os.environ.get("SATEL_LIB"):
    sys.path.insert(0, os.environ["SATEL_LIB"])

import importlib  # noqa: E402

from ethm_simulator import EthmSimulator  # noqa: E402

# Reconnection scenarios wait for real backoffs; the repository default is 10 s.
pytestmark = pytest.mark.timeout(120)

# SATEL_PACKAGE: `satel_integra` (the PyPI package) or another name for a vendored copy of the library
AsyncSatel = importlib.import_module(
    os.environ.get("SATEL_PACKAGE", "satel_integra")
).AsyncSatel


async def _wait_for(condition, limit: float) -> bool:
    end = asyncio.get_running_loop().time() + limit
    while asyncio.get_running_loop().time() < end:
        if condition():
            return True
        await asyncio.sleep(0.1)
    return condition()


async def _connected_client(
    sim: EthmSimulator, zones=(5, 6), outputs=(1,), partitions=(1,)
):
    k = AsyncSatel("127.0.0.1", sim.port, list(zones), list(outputs), list(partitions))
    changes: list[dict] = []
    await k.connect()
    k.register_callbacks(
        zone_changed_callback=lambda z: changes.append(z),
        output_changed_callback=lambda o: None,
        alarm_status_callback=lambda: None,
    )
    await k.start(enable_monitoring=True)
    return k, changes


def test_monitoring_works_on_simulator():
    """Baseline: after connecting, a zone violation reaches the library."""

    async def scenario():
        sim = EthmSimulator()
        await sim.start()
        try:
            k, changes = await _connected_client(sim)
            assert sim.monitoring
            sim.zone_violation(5, True)
            ok = await _wait_for(lambda: any(z.get(5) == 1 for z in changes), 5)
            await k.close()
            return ok
        finally:
            await sim.stop()

    assert asyncio.run(scenario())


def test_monitoring_returns_after_disconnect_and_unanswered_0x7F():
    """BUG in 1.5.0 (satel_integra.py:400-412): after reconnecting there is a single attempt to enable
    monitoring; if the ETHM does not answer, the library waits for the NEXT disconnect - entities stay
    "available", state is frozen."""

    async def scenario():
        sim = EthmSimulator()
        await sim.start()
        try:
            k, changes = await _connected_client(sim)
            sim.faults.no_answer = {0x7F}
            sim.faults.no_answer_times = (
                1  # stay silent only once - afterwards the module answers
            )
            await sim.disconnect()
            reconnected = await _wait_for(lambda: sim.connections >= 2, 10)
            await asyncio.sleep(12)  # 5 s for the missing answer + margin for a retry
            sim.zone_violation(6, True)
            arrived = await _wait_for(lambda: any(z.get(6) == 1 for z in changes), 8)
            result = (reconnected, sim.monitoring, arrived, k.connected)
            await k.close()
            return result
        finally:
            await sim.stop()

    reconnected, monitoring, arrived, connected = asyncio.run(scenario())
    assert reconnected, "the library did not reconnect"
    assert connected
    assert monitoring, "monitoring was not re-enabled on the current connection"
    assert arrived, "zone violation after reconnecting did not arrive"


def test_reconnect_while_enabling_monitoring():
    """BUG in 1.5.0 (connection.py:250): `wait_reconnected()` cleared the event - a connection that dropped
    and came back during the 5 s wait for the 0x7F answer was lost; monitoring stayed disabled."""

    async def scenario():
        sim = EthmSimulator()
        await sim.start()
        try:
            k, changes = await _connected_client(sim)
            sim.faults.no_answer = {0x7F}
            sim.faults.no_answer_times = 1
            await sim.disconnect()  # 1st disconnect -> reconnect -> 0x7F unanswered
            await _wait_for(lambda: sim.connections >= 2, 10)
            await asyncio.sleep(1.5)  # we are now waiting for the 0x7F answer
            await sim.disconnect()  # 2nd disconnect, mid-way
            await _wait_for(lambda: sim.connections >= 3, 20)
            ok = await _wait_for(lambda: sim.monitoring, 25)
            sim.zone_violation(6, True)
            arrived = await _wait_for(lambda: any(z.get(6) == 1 for z in changes), 8)
            await k.close()
            return ok, arrived
        finally:
            await sim.stop()

    ok, arrived = asyncio.run(scenario())
    assert ok, (
        "monitoring did not come back after a disconnect while it was being enabled"
    )
    assert arrived


def test_lost_monitoring_recovered_by_refresh():
    """The ETHM stops sending changes although the connection is up (e.g. the panel behind the module
    restarted). Periodically re-sending 0x7F +12 (per the protocol description the module then sends the
    full state) restores the state without a disconnect."""

    async def scenario():
        package = importlib.import_module(
            os.environ.get("SATEL_PACKAGE", "satel_integra")
        )
        module = importlib.import_module(package.__name__ + ".satel_integra")
        for name in ("MONITORING_REFRESH_INTERVAL", "MONITORING_REFRESH_S"):
            if hasattr(module, name):
                setattr(module, name, 2)
        sim = EthmSimulator()
        await sim.start()
        try:
            k, changes = await _connected_client(sim)
            sim._autonomous.clear()  # the module "forgot" about autonomous mode
            sim.state.violated.add(6)  # a change nobody will send
            arrived = await _wait_for(lambda: any(z.get(6) == 1 for z in changes), 8)
            await k.close()
            return arrived
        finally:
            await sim.stop()

    assert asyncio.run(scenario()), "zone state did not come back without a disconnect"


def test_16_byte_masks():
    """BUG in 1.5.0: the protocol allows 0x00/0x17 with 16 bytes ("+ 16/32 bytes"), the library required 32 -
    a ValueError in frame handling ended the receive loop while TCP stayed open."""

    async def scenario():
        sim = EthmSimulator()
        sim.faults.mask_bytes = 16
        await sim.start()
        try:
            k, changes = await _connected_client(sim)
            sim.zone_violation(5, True)
            ok = await _wait_for(lambda: any(z.get(5) == 1 for z in changes), 5)
            await k.close()
            return ok
        finally:
            await sim.stop()

    assert asyncio.run(scenario())


def test_malformed_frame_does_not_stop_receiving():
    """A 0x00 frame with a wrong length (15 bytes) must be skipped; the next valid one must arrive."""

    async def scenario():
        sim = EthmSimulator()
        await sim.start()
        try:
            k, changes = await _connected_client(sim)
            sim.send_raw(0x00, bytes(15))
            await asyncio.sleep(0.5)
            sim.zone_violation(6, True)
            ok = await _wait_for(lambda: any(z.get(6) == 1 for z in changes), 5)
            await k.close()
            return ok
        finally:
            await sim.stop()

    assert asyncio.run(scenario()), "receiving stopped after a malformed frame"


def _frames(sim, cmd):
    return [r for r in sim.received if r[0] == cmd]


def test_control_without_connection_fails_immediately():
    """BUG in 1.5.0: set_output without a connection returned "success" (command queued without a deadline)."""

    async def scenario():
        sim = EthmSimulator()
        await sim.start()
        try:
            k, _ = await _connected_client(sim)
            sim.faults.busy = (
                True  # after the disconnect the ETHM refuses - no reconnection
            )
            await sim.disconnect()
            await _wait_for(lambda: not k.connected, 5)
            error = None
            try:
                await k.set_output("1234", 7, True)
            except Exception as e:  # noqa: BLE001
                error = type(e).__name__
            await k.close()
            return error
        finally:
            await sim.stop()

    assert asyncio.run(scenario()) == "SatelNotConnectedError"


def test_stale_control_not_sent_after_link_returns():
    """BUG in 1.5.0 ("gate during a disconnect"): an output command waiting in the queue was sent after the
    link came back - the gate moved when nobody wanted it to. Now it is rejected (connection changed / deadline)."""

    async def scenario():
        sim = EthmSimulator()
        await sim.start()
        try:
            k, _ = await _connected_client(sim)
            task = asyncio.create_task(
                k.read_temperature(9)
            )  # zone without a sensor: the panel is silent for 5 s
            await asyncio.sleep(0.3)
            try:
                await k.set_output("1234", 7, True)  # queued behind the read
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.3)
            await sim.disconnect()  # disconnect, reconnect right away
            await _wait_for(lambda: sim.connections >= 2, 10)
            before = len(sim.received)
            await asyncio.sleep(9)  # time in which stock would send the stale 0x88
            after_return = [r for r in sim.received[before:] if r[0] == 0x88]
            task.cancel()
            await k.close()
            return len(after_return), 7 in sim.state.outputs
        finally:
            await sim.stop()

    count, switched_on = asyncio.run(scenario())
    assert count == 0, "stale output command sent after the link came back"
    assert not switched_on


def _client_module():
    package = importlib.import_module(os.environ.get("SATEL_PACKAGE", "satel_integra"))
    return importlib.import_module(package.__name__ + ".satel_integra")


def test_half_dead_link_dropped_and_recovered():
    """REGRESSION: the panel is silent while TCP stays up - the library drops the link itself and reconnects.
    Stock also passes this, because without other traffic its keep-alive gets no answer; the stock bug (not
    detected with frequent commands - keep-alive is then skipped) comes from code analysis and is not
    reproduced here."""

    async def scenario():
        m = _client_module()
        for name, value in (("INBOUND_SILENCE_S", 5), ("KEEPALIVE_INTERVAL", 2)):
            if hasattr(m, name):
                setattr(m, name, value)
        sim = EthmSimulator()
        await sim.start()
        try:
            k, changes = await _connected_client(sim)
            sim.faults.panel_asleep = True
            dropped = await _wait_for(lambda: sim.connections >= 2, 20)
            sim.faults.panel_asleep = False
            ok = await _wait_for(lambda: sim.monitoring, 20)
            sim.zone_violation(6, True)
            arrived = await _wait_for(lambda: any(z.get(6) == 1 for z in changes), 8)
            await k.close()
            return dropped, ok, arrived
        finally:
            await sim.stop()

    dropped, ok, arrived = asyncio.run(scenario())
    assert dropped, "the dead link was not dropped"
    assert ok and arrived, "no monitoring after the panel came back"


def test_no_connection_does_not_burn_cpu():
    """REGRESSION: no connection does not burn the CPU. The busy loop in stock lasts only as long as a single
    TCP attempt - it shows with a "black hole" (SYN without answer, ~2 min), which cannot be reproduced on a
    local port; here we only check that the fix does not break anything."""
    import time

    async def scenario():
        sim = EthmSimulator()
        await sim.start()
        try:
            k, _ = await _connected_client(sim)
            sim.faults.busy = True  # the ETHM refuses - no reconnection
            await sim.disconnect()
            await _wait_for(lambda: not k.connected, 5)
            t0 = time.process_time()
            await asyncio.sleep(3)
            cpu = time.process_time() - t0
            await k.close()
            return cpu
        finally:
            await sim.stop()

    cpu = asyncio.run(scenario())
    assert cpu < 0.5, f"{cpu:.2f} s of CPU in 3 s without a connection"
