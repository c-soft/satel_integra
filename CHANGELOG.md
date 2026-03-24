# Changelog

## [1.0.0](https://github.com/c-soft/satel_integra/compare/0.3.7...1.0.0) - 2026-03-08

This release represents a major internal rewrite of the library. The codebase has been refactored into smaller, dedicated modules, improving maintainability and enabling proper automated testing. This new structure makes it easier to extend the library and allows faster and safer incremental updates going forward.

Two frequently requested improvements are included in this release: command queuing to improve reliability when sending commands rapidly, and encrypted communication support for installations where encryption is required.

### 🚀 Key Improvements

#### Command Queuing

A command queue has been introduced to ensure commands are processed sequentially by the panel. Previously, sending multiple commands in quick succession would cause commands to be ignored  due to the panel’s strict communication flow.

With the new queuing system, commands are now reliably scheduled and processed in order. This significantly improves stability for integrations and automation systems that issue rapid command sequences.

#### Encrypted Communication

Support for encrypted connections has been added, enabling secure communication with panels configured for encryption. This improves compatibility with installations that require encrypted communication and allows the library to be used in more security-sensitive environments.

### ⚠️ Breaking Changes & Migration Guide

Due to the architectural redesign, several APIs have changed. The most important changes and how to adapt existing code are described below.

#### Controller Startup

Previously, the `monitor_status` method handled several responsibilities:

- registering callbacks
- starting the monitoring loop
- enabling monitoring mode
- handling internal communication tasks

This has now been split into two clearer steps:

- `register_callbacks(...)` registers the callbacks
- `start(...)` starts the controller and internal tasks

##### Before

```python
self.controller.monitor_status(
    partitions_update_callback,
    zones_update_callback,
    outputs_update_callback,
)

```
##### After

Callbacks must first be registered, after which the controller is started.
The callback parameters remain in the same order, and monitoring mode is controlled via the `enable_monitoring` parameter.

```python
self.controller.register_callbacks(
    partitions_update_callback,
    zones_update_callback,
    outputs_update_callback,
)

await self.controller.start(enable_monitoring=True)

```
#### Callback Signature Changes

Zone and output callbacks previously received nested dictionaries. In the old signature: `dict[str, dict[int, int]]`, the outer key was always `"zones"` or `"outputs"`.

Callbacks now receive a direct mapping of IDs to their status values, with this signature: `dict[int, int]`
This simplifies the callback interface and removes the unnecessary wrapper structure.

#### Connection Busy Check

The `connect()` method now performs a busy check by default to determine whether the panel is already connected to another application.

If the panel responds with a `Busy` message, the library will treat this as a connection failure.
This behavior can be disabled using the new parameter on the `connect` method: `check_busy`

#### Async `close()`

The `close()` method is now asynchronous because the library needs to properly shut down internal async tasks.
Code calling `close()` must now await it.

#### Keep-Alive Handling

Previously, applications often needed to create a separate task to periodically call `keep_alive`.

This is no longer necessary. When `start()` is called, the library now automatically starts and manages the internal keep-alive mechanism.
Existing manual keep-alive implementations can therefore be removed.

#### Removed `loop` Parameter

The `loop` parameter on `AsyncSatel` has been removed.
It had not been used internally for quite some time and has now been cleaned up as part of the refactor.

### 📦 All Changes

#### Improvements

- #47 - Add ability to disable panel responsiveness check during connect (@Tommatheussen)
- #45 - Adjusting start logic and message clearing (@Tommatheussen)
- #44 - Adjust some connection logic (@Tommatheussen)
- #41 - Handle connection where the device is busy (@Tommatheussen)
- #42 - Add encrypted communication (@wasilukm)
- #37 - Added structured messages (@Tommatheussen)
- #36 - Added connection class (@Tommatheussen)
- #33 - Added structured commands (@Tommatheussen)
- #43 - Improve startup logic (@Tommatheussen)
- #40 - Add message queuing  (@Tommatheussen)

#### Project / CI

- #46 - Set missing license field in pyproject file (@Tommatheussen)
- #39 - Add a release workflow via Github Actions (@Tommatheussen)
- #38 - Fix action issues (@Tommatheussen)
- #35 - Adjust project setup (@Tommatheussen)

## [0.3.7](https://github.com/c-soft/satel_integra/compare/0.3.6...0.3.7) -2022-07-05

- Integrated fix for Python 3.10 compatibility

## 0.3.3 - 2019-03-07

- Added ENTRY_TIME status to display "DISARMING" status in HA
- Fixed issue with unhandled connection error causing HomeAssistant to give up on coommunication with eth module completely

## 0.3.2 - 2019-02-18

- Fixed status issues
- Introduced "pending status"

## 0.3.1 - 2019-02-13

- improved robustness when connection disappears
- fixed issues with "status unknown" which caused blocking of the functionality in HA
- still existing issues with alarm status - to be fixed

## 0.2.0 - 2018-12-20

- Integrated changes from community: added monitoring of ouitputs.
- Attempt at fixing issue with "state unknown" of the alarm. Unfortunately unsuccessful.

## 0.1.0 - 2017-08-24

- First release on PyPI.
