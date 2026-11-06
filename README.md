# Satel Integra: Asynchronous python client for Satel Integra

![PyPI - Version](https://img.shields.io/pypi/v/satel-integra)
![Pypi - Python Versions](https://img.shields.io/pypi/pyversions/satel-integra)
[![Licence](https://img.shields.io/github/license/c-soft/satel_integra)](LICENSE)

[![Build Status](https://github.com/c-soft/satel_integra/actions/workflows/ci.yml/badge.svg)](https://github.com/c-soft/satel_integra/actions/workflows/ci.yml)
[![Open in Dev Containers](https://img.shields.io/static/v1?label=Dev%20Containers&message=Open&color=blue&logo=visualstudiocode)](https://vscode.dev/redirect?url=vscode://ms-vscode-remote.remote-containers/cloneInVolume?url=https://github.com/c-soft/satel_integra)

## About

Communication library and basic testing tool for Satel Integra alarm system. Communication via TCP/IP protocol published by SATEL.

## Installation

```bash
pip install satel_integra
```

## Usage

To use Satel Integra in a project, follow the example, replace values where needed:

```python
from satel_integra import AsyncSatel
```

For complete examples, look into the [examples](examples/) folder.

### Scanning panel resources

After connecting and starting the client, scan the resource types you need:

```python
zones = await satel.scan_zones()
outputs = await satel.scan_outputs()
partitions = await satel.scan_partitions()
```

Each individual scan returns a list of `SatelZoneInfo`, `SatelOutputInfo`, or
`SatelPartitionInfo` objects respectively, ordered by ascending `device_number`.
Omitted resources leave no placeholders; an empty result is `[]`. List positions
are not resource numbers: use each object's `device_number` field.
Scans use panel capacities cached at startup and raise an error if panel
information is unavailable or the panel model is unknown.

All scan methods accept an optional synchronous `on_progress(completed, total)`
callback. Progress counts completed address reads, including addresses omitted
from the result; it does not count discovered devices. Outputs with type code
zero are omitted. The existing scan behavior also omits addresses rejected with
the generic `0x08` result, which panels have been observed to return for unused
addresses. Other read failures and cancellation propagate without returning a
partial result.

Scanning only retrieves metadata; it does not change which resources the client
monitors. To read the full panel inventory, call all three methods sequentially,
as shown above. Each method reports progress independently; callers decide how
to combine results and whether to continue if one scan fails.

## Contributing

Contributions are welcome! If you want to contribute, have a look at [the contribution file](CONTRIBUTING.md).

## License

Refer to the [licence file](LICENCE).

## Features

- [x] Zone violation state
- [x] Output states + toggling
- [x] Alarm panel status + arming/disarming
- [x] Encrypted connections
- [x] Auto reconnecting
- [x] Zone temperature reading
- [ ] Zone tamper/bypass states
- [ ] System faults (bad battery, AC trouble, other errors)
- [ ] System info (panel information, firmware version etc)
- [ ] Event monitoring

## Credits

Original creator of the library is [Krzysztof Machelski](https://github.com/c-soft)

All contributors are listed on [the contributor's page](https://github.com/c-soft/satel_integra/graphs/contributors)
