"""Simulator of the Satel ETHM-1 Plus module of an INTEGRA panel, for testing integrations without a real panel.

The ETHM accepts a SINGLE connection (usually held by Home Assistant), so a live panel cannot be used for
testing from a second machine. This simulator speaks the protocol from the official Satel technical
description (INT-RS v1.14 / INT-RS Plus v2.18 / ETHM-1 Plus v2.05, 2020-03-05) and can inject, on demand,
faults that on a real installation happen rarely and never when you want them: no answer to the
monitoring request, disconnect mid-way, "Busy", command rejection, the panel starting after the ETHM,
disconnect after 25 s of silence.

Frame (description, "Function 2"): FE FE cmd d1..dn crc.hi crc.lo FE 0D; a FE byte in the payload is sent
as FE F0; CRC: start 0x147A, for each byte: rotate left, xor 0xFFFF, + crc.high + b.

Usage in tests (pytest, asyncio):
    sim = EthmSimulator(); await sim.start(); ... sim.port ...; await sim.stop()
    sim.zone_violation(5, True)         # zone 5 violated (sent autonomously if monitoring is enabled)
    sim.faults.no_answer.add(0x7F)      # the module will not answer the monitoring request
    await sim.disconnect()              # drop the connection
From the command line:  python ethm_simulator.py --port 7094
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass, field

_LOG = logging.getLogger("ethm_simulator")

# -- protocol -------------------------------------------------------------------


def crc(data: bytes) -> int:
    """CRC from the Satel description (section "Function 2", Appendix 4)."""
    c = 0x147A
    for b in data:
        c = ((c << 1) & 0xFFFF) | (c >> 15)
        c ^= 0xFFFF
        c = (c + (c >> 8) + b) & 0xFFFF
    return c


def frame(cmd: int, data: bytes = b"") -> bytes:
    payload = bytes([cmd]) + data
    c = crc(payload)
    payload += bytes([c >> 8, c & 0xFF])
    return b"\xfe\xfe" + payload.replace(b"\xfe", b"\xfe\xf0") + b"\xfe\x0d"


class FrameReader:
    """Assembles frames from bytes following rules 1-5 of the description (sync FE FE, FE F0, FE 0D)."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._in_frame = False
        self._fe = False

    def feed(self, data: bytes) -> list[bytes]:
        ready: list[bytes] = []
        for b in data:
            if self._fe:
                self._fe = False
                if b == 0xF0:
                    if self._in_frame:
                        self._buf.append(0xFE)
                    continue
                if b == 0x0D:
                    if self._in_frame and len(self._buf) >= 3:
                        payload, c = (
                            bytes(self._buf[:-2]),
                            (self._buf[-2] << 8) | self._buf[-1],
                        )
                        if crc(payload) == c:
                            ready.append(payload)
                    self._buf.clear()
                    self._in_frame = False
                    continue
                # FE FE or FE + anything else = synchronisation; `b` is already the first byte of the frame
                self._buf.clear()
                self._in_frame = True
                if b == 0xFE:
                    continue
                self._buf.append(b)
                continue
            if b == 0xFE:
                self._fe = True
                continue
            if self._in_frame:
                self._buf.append(b)
        return ready


# -- panel state ------------------------------------------------------------------


def mask(numbers: set[int], size: int) -> bytes:
    """Numbers 1..N -> LSB-first bit mask (byte 0 bit 0 = no. 1), as in the 0x00, 0x0A, 0x17 answers."""
    b = bytearray(size)
    for n in numbers:
        if 1 <= n <= size * 8:
            b[(n - 1) // 8] |= 1 << ((n - 1) % 8)
    return bytes(b)


def from_mask(data: bytes) -> set[int]:
    return {i * 8 + j + 1 for i, x in enumerate(data) for j in range(8) if x & (1 << j)}


@dataclass
class Faults:
    """Faults on demand. Counters: how many more times to trigger (None = always)."""

    no_answer: set[int] = field(
        default_factory=set
    )  # commands the module stays silent on
    no_answer_times: int | None = (
        None  # how many times to stay silent (None = until cleared)
    )
    disconnect_after_command: int | None = (
        None  # drop the connection after receiving this command
    )
    reject_code: int | None = (
        None  # 0xEF with this code for control commands (0x80..0x89)
    )
    busy: bool = False  # every client gets "Busy!" as with an occupied ETHM
    panel_asleep: bool = False  # ETHM accepts TCP, but the panel does not answer
    idle_disconnect_s: float | None = (
        25.0  # ETHM drops the client after this many seconds without a frame
    )
    mask_bytes: int = 32  # 16 or 32 bytes in 0x00 and 0x17 (both legal)
    deaf_outputs: set[int] = field(
        default_factory=set
    )  # outputs that accept 0x88/0x89 (0xEF FF) but do not change
    output_delay_s: float = (
        0.0  # delay between 0xEF and the output change (and the 0x17 report)
    )


@dataclass
class PanelState:
    violated: set[int] = field(default_factory=set)  # 0x00
    outputs: set[int] = field(default_factory=set)  # 0x17
    armed: set[int] = field(default_factory=set)  # 0x0A (mode 0)
    alarm: set[int] = field(default_factory=set)  # 0x13
    exit_time: set[int] = field(default_factory=set)  # 0x0F


_MASK = [
    32
]  # length of zone/output masks - set from `Faults.mask_bytes` on every answer

# read commands we support and their payload (zones and outputs in the 32-byte format - module with bit 0 in 0x7C)
_READS = {
    0x00: lambda s: mask(s.violated, _MASK[0]),
    0x09: lambda s: mask(s.armed, 4),
    0x0A: lambda s: mask(s.armed, 4),
    0x0B: lambda s: mask(set(), 4),
    0x0C: lambda s: mask(set(), 4),
    0x0E: lambda s: mask(set(), 4),
    0x0F: lambda s: mask(s.exit_time, 4),
    0x10: lambda s: mask(set(), 4),
    0x13: lambda s: mask(s.alarm, 4),
    0x14: lambda s: mask(set(), 4),
    0x17: lambda s: mask(s.outputs, _MASK[0]),
    0x2A: lambda s: mask(set(), 4),
}


class EthmSimulator:
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host, self.port = host, port
        self.state = PanelState()
        self.faults = Faults()
        self.received: list[
            bytes
        ] = []  # log of frames from the client (for test assertions)
        self.connections = 0
        self._server: asyncio.base_events.Server | None = None
        self._client: asyncio.StreamWriter | None = None
        self._autonomous: set[int] = (
            set()
        )  # commands sent autonomously after a change (0x7F +12)
        self._silences = 0

    # -- server lifetime --
    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]
        _LOG.info("ETHM simulator on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        await self.disconnect()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def disconnect(self) -> None:
        """Connection dropped by the ETHM side (module restart, network blip)."""
        k, self._client = self._client, None
        self._autonomous.clear()
        if k:
            k.close()
            try:
                await k.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    @property
    def monitoring(self) -> bool:
        return bool(self._autonomous)

    # -- state changes coming from the "panel" --
    def zone_violation(self, zone: int, on: bool) -> None:
        (self.state.violated.add if on else self.state.violated.discard)(zone)
        self._after_change(0x00)

    def output(self, number: int, on: bool) -> None:
        (self.state.outputs.add if on else self.state.outputs.discard)(number)
        self._after_change(0x17)

    def arming(self, partition: int, on: bool) -> None:
        (self.state.armed.add if on else self.state.armed.discard)(partition)
        self._after_change(0x09)
        self._after_change(0x0A)

    def alarm(self, partition: int, on: bool) -> None:
        (self.state.alarm.add if on else self.state.alarm.discard)(partition)
        self._after_change(0x13)

    def _after_change(self, cmd: int) -> None:
        _MASK[0] = self.faults.mask_bytes
        if cmd in self._autonomous and self._client and not self.faults.panel_asleep:
            self._send(cmd, _READS[cmd](self.state))

    # -- connection --
    def send_raw(self, cmd: int, data: bytes) -> None:
        """Any frame to the client (e.g. with a wrong length) - for robustness tests."""
        self._send(cmd, data)

    def _send(self, cmd: int, data: bytes = b"") -> None:
        _MASK[0] = self.faults.mask_bytes
        if self._client:
            self._client.write(frame(cmd, data))

    async def _handle(self, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        if self.faults.busy or self._client is not None:
            w.write(b"Busy!\r\n")
            await w.drain()
            w.close()
            return
        self._client = w
        self.connections += 1
        reader = FrameReader()
        try:
            while True:
                limit = self.faults.idle_disconnect_s
                data = await (
                    asyncio.wait_for(r.read(512), limit) if limit else r.read(512)
                )
                if not data:
                    break
                for payload in reader.feed(data):
                    self.received.append(payload)
                    await self._command(payload)
                    if self._client is not w:
                        return
                await w.drain()
        except (asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            if self._client is w:
                self._client = None
                self._autonomous.clear()
                w.close()

    async def _command(self, payload: bytes) -> None:
        cmd, data = payload[0], payload[1:]
        _MASK[0] = self.faults.mask_bytes
        a = self.faults
        if a.disconnect_after_command == cmd:
            a.disconnect_after_command = None
            await self.disconnect()
            return
        if a.panel_asleep:
            return
        if cmd in a.no_answer and (
            a.no_answer_times is None or self._silences < a.no_answer_times
        ):
            self._silences += 1
            return
        if cmd == 0x7F and len(data) in (12, 14):
            # enable autonomous mode (description: "0x7F +12 bytes ... initially all commands specified ... will be sent")
            half = len(data) // 2
            self._autonomous = {
                c - 1 for c in from_mask(data[:half]) if c - 1 in _READS
            }
            self._send(0xEF, b"\x00")
            for c in sorted(self._autonomous):
                self._send(c, _READS[c](self.state))
            return
        if cmd == 0x7F:
            self._send(0x7F, bytes(5 + len(data)))
            return
        if cmd in _READS:
            self._send(cmd, _READS[cmd](self.state))
            return
        if cmd == 0x1A:
            self._send(
                0x1A, bytes([0x20, 0x26, 0x09, 0x30, 0x12, 0x00, 0x00, 0x00, 0x00])
            )
            return
        if cmd == 0x7C:
            self._send(
                0x7C, b"20520180111" + b"\x01"
            )  # bit0: module reports 32 bytes of zones/outputs
            return
        if cmd == 0x7E:
            self._send(0x7E, bytes([66]) + b"11920200123" + bytes([1, 255]))
            return
        if 0x80 <= cmd <= 0x89:
            if a.reject_code is not None:
                self._send(0xEF, bytes([a.reject_code]))
                return
            self._send(0xEF, b"\xff")
            numbers = (
                from_mask(data[8:]) if len(data) > 8 else set()
            )  # 8 bytes of user code, then the mask
            if cmd in (0x88, 0x89):
                numbers -= a.deaf_outputs
                if a.output_delay_s > 0:

                    async def later(
                        nums=frozenset(numbers), on=cmd == 0x88, t=a.output_delay_s
                    ):
                        await asyncio.sleep(t)
                        for n in nums:
                            self.output(n, on)

                    asyncio.get_running_loop().create_task(later())
                else:
                    for n in numbers:
                        self.output(n, cmd == 0x88)
            elif cmd in (0x80, 0x81, 0x82, 0x83):
                for n in numbers:
                    self.arming(n, True)
            elif cmd == 0x84:
                for n in numbers:
                    self.arming(n, False)
                    self.alarm(n, False)
            return
        # unknown command: the description says to stay silent


async def _control(sim: EthmSimulator, host: str, port: int) -> None:
    """Control port: one JSON line per command, JSON answer. Examples:
    {"cmd":"zone","number":5,"on":true}  {"cmd":"output","number":7,"on":false}  {"cmd":"arming","number":1,"on":true}
    {"cmd":"alarm","number":1,"on":true}  {"cmd":"disconnect"}  {"cmd":"fault","key":"panel_asleep","value":true}
    {"cmd":"state"} -> violated zones, outputs, armed, alarm, monitoring, connections, 0x80..0x89 frames from the client."""
    import json

    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        changes = {
            "zone": sim.zone_violation,
            "output": sim.output,
            "arming": sim.arming,
            "alarm": sim.alarm,
        }
        while line := await r.readline():
            try:
                z = json.loads(line)
                c = z["cmd"]
                if c in changes:
                    changes[c](int(z["number"]), bool(z["on"]))
                elif c == "disconnect":
                    await sim.disconnect()
                elif c == "fault":
                    v = z["value"]
                    setattr(sim.faults, z["key"], set(v) if isinstance(v, list) else v)
                    if z["key"] == "no_answer":
                        sim._silences = 0
                result = {
                    "ok": True,
                    "violated": sorted(sim.state.violated),
                    "outputs": sorted(sim.state.outputs),
                    "armed": sorted(sim.state.armed),
                    "alarm": sorted(sim.state.alarm),
                    "monitoring": sim.monitoring,
                    "connections": sim.connections,
                    "control": [r[0] for r in sim.received if 0x80 <= r[0] <= 0x89],
                }
            except Exception as e:  # noqa: BLE001
                result = {"ok": False, "error": str(e)}
            w.write((json.dumps(result) + "\n").encode())
            await w.drain()
        w.close()

    await asyncio.start_server(handle, host, port)


async def _main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=7094)
    p.add_argument(
        "--control", type=int, default=0, help="JSON control port (0 = disabled)"
    )
    p.add_argument(
        "--control-host",
        default="127.0.0.1",
        help="control port address (in a container: 0.0.0.0)",
    )
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO)
    s = EthmSimulator(a.host, a.port)
    s.faults.idle_disconnect_s = 25.0
    await s.start()
    if a.control:
        await _control(s, a.control_host, a.control)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(_main())
