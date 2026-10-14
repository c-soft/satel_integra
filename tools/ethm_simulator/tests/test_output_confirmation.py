"""Output confirmation - the 0xEF result and the 0x17 report after controlling an output.

Patched library only (stock 1.5.0 has no `set_output_confirmed`, so these tests are skipped there):
    SATEL_LIB=/path/to/patched/library python -m pytest tests -q
"""

import asyncio
import importlib
import os

import pytest
from test_reconnection import EthmSimulator, _connected_client, _wait_for

PACKAGE = importlib.import_module(os.environ.get("SATEL_PACKAGE", "satel_integra"))
pytestmark = [
    pytest.mark.skipif(
        not hasattr(PACKAGE.AsyncSatel, "set_output_confirmed"),
        reason="the library does not confirm outputs yet",
    ),
    pytest.mark.timeout(120),
]
CODE = "1234"


def _scenario(prepare, action):
    async def whole():
        sim = EthmSimulator()
        await sim.start()
        try:
            k, _ = await _connected_client(sim, outputs=(1, 7))
            prepare(sim)
            try:
                return await action(sim, k)
            finally:
                await k.close()
        finally:
            await sim.stop()

    return asyncio.run(whole())


def test_accepted_and_reported():
    async def d(sim, k):
        result = await k.set_output_confirmed(CODE, 7, True)
        reported = await _wait_for(lambda: 7 in k.violated_outputs, 3)
        return result, reported, sim.state.outputs

    result, reported, outputs = _scenario(lambda s: None, d)
    assert result.is_success and reported and 7 in outputs


def test_panel_rejection_is_error_with_code():
    async def d(sim, k):
        with pytest.raises(PACKAGE.SatelCommandRejectedError) as e:
            await k.set_output_confirmed(CODE, 7, True)
        return int(e.value.result.code), sim.state.outputs

    code, outputs = _scenario(lambda s: setattr(s.faults, "reject_code", 0x02), d)
    assert code == 0x02 and 7 not in outputs


def test_no_answer_is_error():
    def p(s):
        s.faults.no_answer = {0x88}

    async def d(sim, k):
        with pytest.raises(PACKAGE.SatelNoResponseError):
            await k.set_output_confirmed(CODE, 7, True)
        return True

    assert _scenario(p, d)


def test_deaf_output_accepted_but_not_reported():
    """0xEF FF, but the output does not change: the library raises no error (the decision belongs to the
    entity - a warning in the log, state from the panel), but it does not pretend the output is on either."""

    async def d(sim, k):
        result = await k.set_output_confirmed(CODE, 7, True)
        reported = await _wait_for(lambda: 7 in k.violated_outputs, 3)
        return result, reported

    result, reported = _scenario(lambda s: setattr(s.faults, "deaf_outputs", {7}), d)
    assert result.is_success and not reported


def test_delayed_report_arrives():
    async def d(sim, k):
        await k.set_output_confirmed(CODE, 7, True)
        immediately = 7 in k.violated_outputs
        reported = await _wait_for(lambda: 7 in k.violated_outputs, 3)
        return immediately, reported

    immediately, reported = _scenario(
        lambda s: setattr(s.faults, "output_delay_s", 1.0), d
    )
    assert not immediately and reported
