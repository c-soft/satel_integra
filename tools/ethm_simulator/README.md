# ETHM-1 Plus simulator

A small asyncio TCP server that behaves like a Satel ETHM-1 Plus module in front of an INTEGRA panel,
plus a pytest suite that runs `satel_integra` against it. An ETHM accepts only one client, so a live
panel that is already used by Home Assistant cannot serve as a test target; the simulator also makes it
possible to trigger faults that a real installation produces rarely and never on demand.

## What it emulates

Based on the official Satel protocol description (INT-RS v1.14 / INT-RS Plus v2.18 / ETHM-1 Plus v2.05,
2020-03-05):

- **Frame format**: `FE FE cmd d1..dn crc.hi crc.lo FE 0D`; a `FE` byte inside the frame is sent as `FE F0`.
- **CRC**: start `0x147A`; for each byte rotate left, xor `0xFFFF`, add `crc.high + byte`.
- **Read commands**: `0x00` (violated zones), `0x09`/`0x0A` (armed partitions), `0x0F`, `0x13` (alarm),
  `0x17` (outputs) and a few others returning empty masks; `0x1A` (RTC), `0x7C` (module version, bit 0 set:
  32-byte zone/output masks), `0x7E` (panel version).
- **`0x7F` + 12/14 bytes, monitoring (autonomous) mode**: answered with `0xEF 00`, then the module sends the
  full state of every selected command and afterwards sends each of them again whenever it changes.
  `0x7F` with any other length is answered with a zero-filled `0x7F` frame.
- **`0x80..0x89` control commands**: answered with an `0xEF` result (`FF` = accepted, or a configurable
  rejection code). `0x88`/`0x89` switch outputs, `0x80..0x83` arm, `0x84` disarms and clears the alarm.
- **Unknown commands**: no answer, as the description requires.
- **16/32-byte masks**: `0x00` and `0x17` may carry 16 or 32 bytes ("+ 16/32 bytes"); both are legal and
  selectable.

ETHM-1 Plus specific behaviour that is **not** in the protocol document:

- **`Busy!`**: a second client (while one is connected) gets `Busy!\r\n` and the socket is closed.
- **Idle disconnect**: the module drops a client that has sent no frame for 25 s.

## Faults

`EthmSimulator.faults` (a `Faults` dataclass) can be changed at any time:

| field | effect |
| --- | --- |
| `no_answer` | set of commands the module stays silent on |
| `no_answer_times` | how many times to stay silent (`None` = until cleared) |
| `disconnect_after_command` | drop the connection after receiving this command (one-shot) |
| `reject_code` | answer control commands with `0xEF` and this code |
| `busy` | every new client gets `Busy!` |
| `panel_asleep` | TCP is accepted, but nothing is answered or reported |
| `idle_disconnect_s` | idle timeout (default 25.0, `None` = never) |
| `mask_bytes` | 16 or 32 bytes in `0x00` and `0x17` |
| `deaf_outputs` | outputs that accept `0x88`/`0x89` (`0xEF FF`) but never change |
| `output_delay_s` | delay between the `0xEF` result and the output change (and its `0x17` report) |

State changes from the "panel" side: `zone_violation(n, on)`, `output(n, on)`, `arming(n, on)`,
`alarm(n, on)`; `await disconnect()` drops the client; `send_raw(cmd, data)` sends an arbitrary frame.
`received` logs every frame from the client, `connections` counts accepted connections, `monitoring`
says whether autonomous mode is active.

## Running standalone

```
python ethm_simulator.py --port 7094 --control 7095
```

Options: `--host` (default 127.0.0.1), `--port` (default 7094), `--control` (JSON control port, 0 = disabled),
`--control-host` (default 127.0.0.1; use 0.0.0.0 in a container). Point the integration at host/port.

## Control port protocol

One JSON object per line; every answer is one JSON line with the current state
(`violated`, `outputs`, `armed`, `alarm`, `monitoring`, `connections`, `control` = command bytes
`0x80..0x89` received from the client) or `{"ok": false, "error": "..."}`.

```
{"cmd":"zone","number":5,"on":true}
{"cmd":"output","number":7,"on":false}
{"cmd":"arming","number":1,"on":true}
{"cmd":"alarm","number":1,"on":true}
{"cmd":"disconnect"}
{"cmd":"fault","key":"panel_asleep","value":true}
{"cmd":"fault","key":"no_answer","value":[127]}
{"cmd":"state"}
```

For `fault`, a JSON list becomes a set; setting `no_answer` also resets the silence counter.

## Tests

The library is selected with environment variables: `SATEL_LIB` (directory containing the package; when
unset the installed package is used) and `SATEL_PACKAGE` (package name, default `satel_integra`).
Each run takes about a minute.

On `master` before these fixes (1.5.0), 7 tests fail; each is fixed by one PR:

| Tests | Fixed by |
|---|---|
| `test_monitoring_returns_after_disconnect_and_unanswered_0x7F`, `test_reconnect_while_enabling_monitoring`, `test_lost_monitoring_recovered_by_refresh` | #81 |
| `test_control_without_connection_fails_immediately`, `test_stale_control_not_sent_after_link_returns` | #82 |
| `test_16_byte_masks`, `test_malformed_frame_does_not_stop_receiving` | #83 (merged) |

The baseline test and the two REGRESSION tests pass everywhere. `test_output_confirmation.py` is skipped
until the library has `set_output_confirmed` (item 5 of #79, to be done together with Home Assistant).

From the repository root (the tests set their own 120 s timeout; the suite takes about a minute):

```
python -m pytest tools/ethm_simulator/tests -q --no-cov
```

To test another checkout or a vendored copy, point `SATEL_LIB` at the directory that contains the package.
