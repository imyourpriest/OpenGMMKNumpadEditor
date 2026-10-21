# Validation record

OpenGMMK Numpad Editor has been reported to work well by its initial user. The protocol and hardware-free checks below provide additional evidence. Detailed physical acceptance for all supported settings, unplug/replug persistence, and another-computer operation remain unverified.

## Reviewed environment and implementation

The initial development environment was Ubuntu 26.04 with GNOME. Checks recorded on 2026-10-14 established:

- The GTK editor starts and renders, with scrolling for smaller windows.
- USB discovery identifies the stock `320f:5088`, revision `0088`, and its interfaces `00`, `01`, and `02`.
- Configuration interface `02` declares feature report `07`, 264 bytes including its report ID. Its descriptor matches the manufacturer's transport and the app's identity guard.
- Independent static review matched key tables, modifier encoding, profile/layer selection, RGB commands, and status offsets against official CORE 1.1.36 resources. The Windows installer and application were not executed.
- The existing stock-firmware Ubuntu slider integration was retained separately.

The original development access check preceded setup of the configuration interface. It is historical evidence, not a statement about current user permissions. The initial user subsequently reported on 2026-10-15 that the application worked well and repeated that feedback on 2026-10-20. That feedback is useful acceptance evidence, but it does not identify every setting tested or establish a power-cycle result.

## Automated checks

Run from the project folder:

```sh
python3 -B -m unittest discover -s tests -v
```

The suite uses simulated devices and temporary installation folders. It covers:

- Profile round trips and migration, malformed imports, and atomic file saving.
- Exact protocol examples, key and LED indices, modifier encoding, reserved bytes, and whole-layer/overlay replacement.
- Device identity changes, incomplete transfers, concurrent access, and operation sequencing.
- Installation/removal behavior, including preservation of added or modified files.
- Monitor rate limits, cache expiry, connection changes, and exact Bluetooth/UPower battery association.
- Indicator labels, D-Bus menu behavior, and missing or stale battery readings.

These tests do not send HID reports to real hardware. Continuous integration runs this suite on Linux. A separate GTK construction check requires the desktop dependencies and a graphical session:

```sh
sh run.sh --smoke-test
```

The 0.2.0 validation run on 2026-10-20 passed all **182 tests**. This is a dated result; use a fresh test run or CI for the current count. Passing software checks does not establish that firmware accepted a setting or retained it in onboard storage.

## Connection and battery monitoring: 2026-10-20

One live USB status query returned battery **100%** and firmware word/version **`0088`** from the supported stock numpad. This confirms the status path returned a plausible live reading on this device; it does not establish battery accuracy, charging semantics, or continuous monitoring behavior.

A separate read-only operating-system check found the already paired Bluetooth numpad with Modalias `usb:v093Ap2860d0001`, disconnected while USB was in use. It advertised Battery Service `180F` and HID Service `1812`. No `org.bluez.Battery1` instance or UPower battery for the numpad was available in that state. Other UPower devices were not used as substitutes.

An isolated instance of the integrated application, using a validation application ID, registered with the actual GNOME `org.kde.StatusNotifierWatcher`. An independent D-Bus query showed its `org.kde.StatusNotifierItem-<process-id>-1` entry in `RegisteredStatusNotifierItems`. Its exported indicator label was **USB 100%**, and its monitor returned battery 100% and firmware `0088`.

A programmatic window-lifecycle check marked a temporary draft as unsaved, hid the editor window, and reopened it. The same editor instance and unsaved state remained. After clearing the temporary test state, the application quit cleanly. These checks establish integration and retained draft state; a physical check of the unsaved-quit dialog remains pending.

After the 0.2.0 desktop installation was launched, an independent D-Bus query again read **USB 100%** from the running application's `XAyatanaLabel`. The user then confirmed that the actual top-bar icon, USB 100% display, and **Open editor**, **Refresh status**, and **Quit** menu actions worked. Bluetooth battery delivery still requires a physical connected-device test.

The monitoring implementation is intended to keep connection discovery to approximately five-second intervals, USB status queries to 30-second intervals, and failed-refresh battery cache lifetime to 120 seconds. It uses the existing configuration-interface lock, does not infer charging from the undocumented USB field, and reads Bluetooth information only through OS APIs. Consult the current automated test output for the validated failure and cache cases.

## Compact indicator: 2026-10-20

Version 0.2.1 replaces the top-bar text with a symbolic numpad, an integrated USB/Bluetooth badge, and a battery bar. Exact connection and battery details remain in the menu, tooltip, and accessible description. The bar uses 5% steps, with a visible sliver for a nonzero reading below 5%; only a measured 100% fills it completely. Unavailable or stale readings use a dashed bar, distinct from a measured 0%.

The source validation passed all **191 headless tests**, including battery boundaries, stale/invalid/disconnected states, icon-change notifications, retained exact menu percentages, and accessibility updates. The graphical construction smoke test also passed. All 47 generated SVG assets passed their reproducibility check and loaded through GTK's symbolic icon lookup at 22 pixels using both light and dark foregrounds. A contact sheet was visually checked at normal and enlarged sizes.

After installation, the previous app exited through its normal menu Quit callback and the installed 0.2.1 application was relaunched. Independent session-bus readback found its registered icon `gmmk-numpad-usb-100-symbolic` in the bundled tray directory, empty label/label guide/overlay, and exact **USB connected / Battery: 100%** tooltip text. Its accessible description also retained connection and battery information. All **191 tests** passed in the installed copy, and its launch log was empty. Physical user confirmation of the compact icon's appearance remains pending.

These checks validate presentation and integration support; they do not add physical Bluetooth battery or onboard persistence acceptance.

## Physical acceptance checklist

Record the USB identity/revision, firmware information when available, desktop environment, target profile/layer, and exact settings used for each test.

- [x] Read battery and firmware status over USB once: 100%, firmware `0088`, on 2026-10-20.
- [x] Register the integrated indicator with GNOME's actual host and independently read its USB 100% label over D-Bus.
- [x] Programmatically hide/reopen the same editor with an unsaved draft retained, then quit cleanly after clearing test edits.
- [x] User confirmed the visible top-bar icon, USB 100% display, and Open editor / Refresh status / Quit actions on 2026-10-20.
- [ ] Close the editor window; reopen the same unsaved draft from the indicator; confirm Quit handles unsaved edits.
- [ ] Disconnect/reconnect USB; confirm the indicator follows connection changes and marks/expires unavailable readings correctly.
- [ ] Confirm a configuration write does not interleave with automatic status polling.
- [ ] Connect the numpad over Bluetooth; confirm the correct device and an OS-provided battery percentage, or a clear unavailable state.
- [ ] Check behavior without a StatusNotifier host; closing the editor must not leave an inaccessible running app.
- [ ] Save a complete intended key layer; check Print Screen, ordinary keys, and modifier combinations.
- [ ] Check knob turning, slider volume, unmute behavior, and the desktop volume popup if the separate slider helper is installed.
- [ ] Save and check each global RGB effect, color, speed, and brightness setting.
- [ ] Add a static per-key color, remove it, and remove the last override; confirm the expected global effect returns.
- [ ] Save Off and confirm global lighting and custom-color overlays clear. The separate knob/key glow setting is outside this operation.
- [ ] Activate each intended profile/layer and confirm the expected settings.
- [ ] Disconnect/reconnect USB and check the same slot/layer again.
- [ ] Connect to another computer without CORE, the editor, or a conflicting host remapper running; check the saved assignments and lighting.
- [ ] Check ordinary stock Bluetooth use separately; this editor only configures over USB.

Temporarily disable only overlapping host-side numpad mappings during an onboard mapping test. Existing onboard keybindings and lighting cannot be read back or backed up using the known status command. Keep intended layouts in local profile files before testing changes.

For reproducible hardware reports, use the information in [CONTRIBUTING.md](CONTRIBUTING.md). Do not infer onboard persistence from a local JSON save or an accepted USB transfer.
