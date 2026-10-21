# Development handoff

OpenGMMK Numpad Editor is a native GTK application for the stock-firmware Glorious GMMK Numpad. This repository is the source of truth for future changes. Use its folder as a dedicated Codex project and start a new chat there for each focused feature or fix. The original Ubuntu setup conversation is historical context; it is not needed to build or test the application.

The development checkout is registered in Codex as **OpenGMMKNumpadEditor**. Open that project for future work so the app's source and tests are available directly; a new chat there can use this handoff.

The public repository is [imyourpriest/OpenGMMKNumpadEditor](https://github.com/imyourpriest/OpenGMMKNumpadEditor). Keep the source checkout, installed application, and portable distribution distinct. Edit and test the checkout, then use the installer to update the desktop copy while preserving profile files. Do not silently patch the installed copy alone.

## Application layout

| File | Responsibility |
| --- | --- |
| `app.py` | GTK profile editor, device-operation worker coordination, window lifecycle, and monitor presentation. |
| `profile.py` | Profile format, validation, version migration, and atomic local saves. |
| `protocol.py` | Pure encoders and status parser for the reviewed stock USB protocol. |
| `device.py` | Read-only sysfs discovery and supported-device selection. |
| `transport.py` | Strict Linux hidraw identity checks, allowlisted framing, and exclusive nonblocking lock. |
| `backend.py` | Short synchronous status/configuration operations, run outside the GTK thread. |
| `monitoring.py` | Read-only USB/BlueZ/UPower monitoring, rate limits, and bounded battery cache. |
| `status_notifier.py` | Session-bus StatusNotifierItem/D-BusMenu indicator; no direct hardware access. |
| `installation.py`, `install.sh`, `uninstall.sh` | Per-user desktop installation and managed-file removal. |
| `install-device-access.sh` | Separate narrowly scoped Ubuntu USB-access setup. |
| `assets/` | Original icon and its rendering utility. |
| `tests/` | Hardware-free checks using simulated transports and temporary files. |

## Preserve these behaviors

- Keep stock firmware, Bluetooth operation, slider behavior, and the working separate Ubuntu slider/OSD helper. Do not flash QMK/VIA or change that helper for an editor feature.
- Configuration supports only USB `320f:5088`, revision `0088`, interface `02`, with the exact descriptor in [PROTOCOL.md](PROTOCOL.md). Keep validation against current kernel metadata before feature exchanges. A product name or vendor ID alone is insufficient.
- Send only evidenced, explicitly allowed commands. No blind command probes, replay interface, firmware/bootloader operations, broad HID permissions, or root GUI.
- A local profile is an intended configuration, not a readback or backup of onboard state. Keymap and RGB operations replace complete documented records. Do not claim persistence without a physical test.
- Preserve the transport lock across complete transactions. Status polling yields to a busy device. GTK stays responsive while hardware and D-Bus work run in a worker.
- Monitor Bluetooth through BlueZ/UPower read-only APIs. Never change pairing, connection, discovery, trust, or GATT state merely to obtain a battery value. Bind a percentage to the exact uniquely selected device.
- Keep unknown charging values unknown. USB connectivity indicates USB power, not proven charging. Missing and stale battery readings must not look current.
- Closing to a working indicator retains the draft in memory; quitting still offers to preserve unsaved edits. Without an indicator host, the editor must remain recoverable and closable. Do not install login autostart without an explicit product decision.
- Do not bundle CORE source, installers, application archives, firmware, or databases. Original interoperability code and protocol facts belong here; manufacturer artifacts do not.

## Local checks

From the repository folder:

```sh
python3 -B -m unittest discover -s tests -v
sh run.sh --smoke-test
```

The test suite needs Python 3.10 or newer on Linux. The UI also requires GTK 3.24 and PyGObject; see [README.md](README.md). The smoke test requires a graphical session. Do not make real HID access or administrator privileges prerequisites for the automated suite.

Add tests for material behavior and failure handling: device identity changes, malformed imports, exact protocol bytes, cache expiry and transport changes, mismatched Bluetooth batteries, tray callbacks, and installation preservation. Do not write tests that merely repeat an implementation expression.

For changes affecting hardware, record a small live acceptance result separately from software tests. USB `8A` is the only status request evidenced here; existing onboard settings cannot be read back. Before testing a key that also has a host-side mapping, temporarily disable only that overlapping numpad mapping so it cannot mask the result. Do not disturb unrelated mappings.

## Current monitoring scope

The 0.2 monitoring work adds an optional desktop indicator while the app runs, connection discovery about every five seconds, USB battery reads about every 30 seconds, and a 120-second maximum cache after a failed refresh. No login autostart is installed. BlueZ identifies the stock Bluetooth numpad as `093a:2860`; a paired but disconnected instance was observed on 2026-10-20. It advertised the standard battery service, but Bluetooth delivery still needs a physical connected-device test.

One live USB query on 2026-10-20 returned battery 100% and firmware `0088`. Treat this as one status-path observation, not battery calibration or proof of charging. [VALIDATION.md](VALIDATION.md) tracks the remaining physical checks. Keep evidence accurate as the feature changes; do not freeze an automated test count in prose.

## Documentation and distribution

Update README behavior and limitations when changing the user flow. Update protocol evidence for any changed command or supported identity, and record actual acceptance in VALIDATION. Keep generated HTML guides synchronized when preparing a portable package. Installation/removal must continue to preserve added or modified files and user profile documents. Before publishing, check the diff and distribution contents for personal paths, Bluetooth addresses, credentials, manufacturer artifacts, and development-only files.
