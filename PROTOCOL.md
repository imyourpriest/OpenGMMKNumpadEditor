# Stock GMMK Numpad USB protocol evidence

This is an interoperability record for the protocol subset used by OpenGMMK Numpad Editor. The packet format was independently reviewed against resources packaged in the official manufacturer's application. The initial user reports that the editor works well. Specific key/LED combinations, power-cycle persistence, and another-computer behavior still need recorded physical acceptance results; source review and successful USB transfers alone do not establish those results.

## Supported device and transport

| Field | Required value |
| --- | --- |
| USB vendor/product | `320f:5088` |
| USB device revision | `0088` |
| USB interface | `02` |
| HID report type | Feature |
| Report ID | `07` |
| Report count | 263 eight-bit payload elements |
| Complete transfer size | 264 bytes, including report ID |
| Report descriptor SHA-256 | `fda4cd2490bbe43df640a5f6f21ba23a2c586ada787d40d777d637d675015aed` |

Every packet below is zero-filled to 264 bytes. Byte `0` is `07`; byte `1` is the command. Profile and layer fields use values `1` through `3`. There is no checksum in the evidenced frames. Do not apply protocols for the original SONiX GMMK or for QMK/VIA firmware to this device.

CORE's device discovery tries usage page/usage `FF02:0001`, then `FF01:0001`, for each supported device identity (`Device.js:319–357` and the Numpad record in `SupportDevice.DB`). The native transport uses HIDAPI feature reports (`GmmkNumpadSeries.js:2060–2093`, `hiddevice.cc:565–655`). The vendor wrapper removes the first report-ID byte from returned data; this application's raw transport retains it. All response offsets in this document include the report ID.

## Evidence provenance

The [official legacy software page](https://www.gloriousgaming.com/pages/legacy-software) links [GloriousCore.zip](https://downloads.gloriousgamingservices.com/download/GloriousCore.zip). On 2026-10-14 it contained `GloriousCore_Setup_1.1.36.exe`. The archive and application resources were extracted statically; the installer and application were not executed.

| Evidence | SHA-256 |
| --- | --- |
| `GloriousCore_Setup_1.1.36.exe` | `7f68626ebf7d61659629808d48936d5f404921a4728624f146e99acbc07e4f9d` |
| `app/resources/app.asar` | `f7bfe092a9d59380cfab27ad4428ccd67e9e6dbeb402eaf5501e93f796404524` |
| `GmmkNumpadSeries.js` | `8820d9b28a279dcb5d5db5e0e6399d50aa9b584164c4b9d04729f75a17c1dffe` |
| `GMMKLocation.js` | `724a5c4d0832af3570479cf39bb4728c8fdbb0a0d9281a55b7de17d7321a1417` |
| `SupportData.js` | `0b72a0b6d93e13746489a0fab7e6a272599f8f4ae50af69a2c7870595cc42f92` |
| `hiddevice.cc` | `cb553f4f1cad4e52134ae65fccb30c92c68fb47ce40f679c968573fe12cff594` |
| `LayoutDB.db` | `50944c3a8375d31b87d0a0a8505f4a33f6fe3b01f08253329a9c2497a322a631` |
| `M_Light_Numpad.ts` | `3e1ad6880b214d580958ba8fbf3caf82a726bf9eb83a7b187c7022797686f197` |

Paths inside the ASAR are:

- `src/electron-process/renderer-interop/protocol/device/keyboard/GmmkNumpadSeries.js`
- `src/electron-process/renderer-interop/protocol/device/Device.js`
- `src/electron-process/renderer-interop/others/GMMKLocation.js`
- `src/electron-process/renderer-interop/others/SupportData.js`
- `src/electron-process/renderer-interop/database/SupportDevice.DB`
- `src/electron-process/renderer-interop/database/LayoutDB.db`
- `src/electron-process/hiddevice/src/hiddevice.cc`
- `src/renderer-process/components/KeyboardNumpadComponent/Built_ineffect.class.ts`
- `src/renderer-process/components/KeyboardNumpadComponent/M_Light_Numpad.ts`
- `src/renderer-process/components/KeyboardNumpadComponent/keyboard-numpad.component.ts`
- `src/renderer-process/components/KeyboardNumpadComponent/keyboard-numpad.component.html`

The hashes and internal archive paths identify the evidence used during review. No extracted manufacturer source, installer, application archive, firmware, or database is included in this project. The application has no runtime dependency on CORE.

## Status query: `8A`

`GmmkNumpadSeries.js:2325–2378` builds `07 8A`, sends the 264-byte feature report, waits 30 ms, reads feature report `07` with length 264, then waits another 30 ms.

| Raw response offset | Meaning evidenced by CORE |
| --- | --- |
| `0` | Report ID |
| `1` | Status command position |
| `2` | Battery percentage, checked against 0–100 |
| `3` | Charging value; the source does not define its enumeration |
| `4–5` | Firmware word, little-endian |

CORE interprets the firmware word's hexadecimal digits as a padded version string. This application can retain the raw word without assuming every firmware version is valid BCD. No keymap, RGB, profile, or current-setting readback command was found in the Numpad implementation.

The response's byte `1` is retained for diagnostics, rather than treated as a required `8A` echo: no such validation was found in the reviewed manufacturer code. The unknown charging byte is retained as raw information by the manual status action. Automatic monitoring does not translate it into a charging state.

### Automatic USB monitoring

The monitor reuses this exact status operation, rather than adding commands. A nonblocking exclusive lock on the configuration interface covers the status request, delays, and response; the same lock protects configuration writes. If another editor operation owns the device, monitoring yields and keeps its bounded cache instead of interleaving packets or retrying configuration.

Connection discovery runs about every five seconds; USB battery queries are limited to about one every 30 seconds. The last successful battery reading can be retained for at most 120 seconds when refresh fails and is marked stale. Changing the selected physical device or transport clears the old reading. Monitoring is read-only at the device-setting level: its one USB feature SET is the manufacturer's status request and contains no configuration payload.

## Bluetooth and desktop battery monitoring

Bluetooth monitoring uses the operating system's public D-Bus APIs, with no vendor reports, GATT reads, pairing, connection changes, property writes, or service auto-start. The manufacturer device database identifies the stock Bluetooth identity as `093a:2860`; a read-only check on 2026-10-20 also found the paired numpad with Modalias `usb:v093Ap2860d0001`. It was disconnected because USB was in use. Its advertised service list included the standard Battery Service `180F` and HID Service `1812`; this is evidence of advertised services, not a completed Bluetooth battery test.

The [BlueZ Device API](https://bluez.readthedocs.io/en/latest/device-api/) supplies the optional `Modalias`, `Paired`, and `Connected` properties on `org.bluez.Device1`. Battery readings require a unique connected numpad whose vendor/product identity matches the supported identity. A name-only match may describe connection availability but does not authorize a battery association. Conflicting or ambiguous identities are not resolved by guessing.

The [BlueZ Battery API](https://bluez.readthedocs.io/en/latest/battery-api/) supplies `org.bluez.Battery1.Percentage`, a read-only byte, on that same device object. It has no charging-state property. If no precise BlueZ reading is available, an optional [UPower Device API](https://upower.freedesktop.org/docs/Device.html) fallback accepts only a battery whose `NativePath` exactly identifies the selected BlueZ device or its complete Bluetooth address. Model names and unrelated mouse, keyboard, UPS, or laptop batteries are insufficient. A UPower reading must be present, device-local, and a precise percentage; coarse battery levels are not converted into invented percentages. Only UPower's documented `State` may supply a charging state.

Neither a USB cable nor an undocumented nonzero USB status byte is presented as proof of charging. Missing, stale, and ambiguous readings remain distinguishable in the user interface. Bluetooth addresses and the full device inventory are not exposed in the monitor's public status snapshot.

The top-bar presentation uses StatusNotifierItem and D-BusMenu on the user session bus. This does not grant additional HID or Bluetooth privileges. It depends on a compatible desktop host, such as Ubuntu's AppIndicators extension. No background system service or login autostart entry is installed for monitoring.

## Whole-layer key assignments: `03`

`GmmkNumpadSeries.js:1291–1390,1486–1607` and `GMMKLocation.js:304–339` establish the key table.

The header is `07 03 <slot> <layer> 00 00 00 00`. Bytes `8–27` are 20 key types; bytes `28–47` are their 20 action values. The remaining bytes are zero. Type `01` is a normal keyboard usage. Supported generic modifier combinations use type `E0 + mask`, with Ctrl=`1`, Shift=`2`, Alt=`4`, Super=`8`; the action value remains the keyboard usage.

The matrix indices are:

| Index | Key | Index | Key |
| --- | --- | --- | --- |
| 0 | Num Lock | 1 | Divide |
| 2 | Multiply | 3 | Subtract |
| 4 | 7 | 5 | 8 |
| 6 | 9 | 7 | Add |
| 8 | 4 | 9 | 5 |
| 10 | 6 | 11 | Encoder press |
| 12 | 1 | 13 | 2 |
| 14 | 3 | 15 | Enter |
| 16 | 0 | 17–18 | Empty |
| 19 | Decimal | | |

The builder starts with the manufacturer's complete table and replaces the 17 editable key entries. Index `11` remains type `03`, value `10`: the manufacturer's calculator action. This overwrites a custom encoder press assignment. Rotary turning and the slider are not entries in this editable key table. Command `09`, which separately carries knob sound-control/glow state, is not sent by this application.

## Global RGB: `02`, then settings `07`

This application's lighting path is always **`02` → `07` → `06` → `01`**. The global effect and base color go through `02`; `06` sends the complete custom-color overlay, including erased entries. The official path uses the same order and sends `06` when its selected layout is present (`GmmkNumpadSeries.js:1079–1119,1722–1751`). Sending `02` alone is not an evidenced complete operation; this source does not prove whether it immediately takes effect or persists by itself.

For command `02`, see lines `1753–1800`:

| Offset | Value |
| --- | --- |
| `2–3` | Slot, layer |
| `4` | Effect ID |
| `8` | `trunc(20 - speed_percent × 19 / 100)` |
| `9` | `00` in the newer firmware path |
| `10` | Multicolor flag when supported; this app uses `00` |
| `12` | `01` for Rainbow, otherwise `00` |
| `15–17` | Red, green, blue |

The application exposes these raw effect IDs: off=`0`, Wave #1=`2`, breathing=`6`, static/Normally On=`7`, reactive/Ripple Graff=`8`, rainbow=`13`. The class's constructor at lines `24–45` defines the translation from UI effect values. `Built_ineffect.class.ts:8–22,58–73,100–127,161–170` confirms the relevant UI options; Rainbow and Off disable multicolor.

For command `07`, see lines `1802–1860`:

| Offset | Value or explicit choice |
| --- | --- |
| `2–4` | Slot, layer, same effect ID |
| `5` | Win Lock=`00` |
| `6` | Per-key enable=`01` |
| `7` | Num Lock LED indicator, `00` or `01` |
| `8` | USB brightness, `trunc(percent × 20 / 100)` |
| `10` | Debounce/latency byte=`08`; the configurable assignment is commented out |
| `11` | Sensitivity, `trunc(value / 2)` for a UI value 0–100 |
| `12` | Battery indicator, `00` or `01` |
| `13` | Bluetooth brightness, `trunc(percent × 20 / 100)` |

Off forces brightness bytes `8` and `13` to decimal `10`, matching CORE. Unlisted bytes are zero. Each of these two reports is followed by 100 ms.

The settings packet is not a brightness-only update. Its other fields must be visible, explicit choices because no known readback can preserve their current values. `SupportDevice.DB`, first record, ships all nine Numpad layers with sensitivity `2`, Num Lock indicator false, battery indicator true, Win Lock false, and USB brightness 100. Its stored Bluetooth brightness is `0`, but its absent `brightnessFlag` makes CORE use USB brightness instead (`GmmkNumpadSeries.js:1835–1842`). These are CORE defaults, not verified firmware factory or current values.

## Per-key RGB: `06`

`GmmkNumpadSeries.js:1889–2058` builds:

`07 06 <slot> <layer> 2A 00 <brightness_0_to_20> 00`

Bytes `8–217` hold 42 five-byte records. A populated record at matrix position `j` is `[visibility, j, red, green, blue]`: `00` clears the custom-color entry, `FF` enables a static color, and `FE` enables breathing. Bytes `218–263` are zero. The source writes one page and waits 100 ms.

The official Numpad layout in `LayoutDB.db`, line 1, `AllData[8]`, starts with all 33 UI entries at `clearStatus=false`, zero RGBA, and breathing false. `M_Light_Numpad.ts:130–159` erases entries by restoring those values; `keyboard-numpad.component.ts:1053–1062` explicitly calls that state “PERKEY Erase.” `LayoutToData` maps it to visibility `00`. The official preview renders the global effect below a per-key overlay (`keyboard-numpad.component.html:131–148`); cleared RGBA entries are transparent. This supports interpreting per-key colors as overrides of the global effect. Physical firmware composition still needs acceptance testing.

This application's builder therefore sends `[00, j, 00, 00, 00]` for each of the 31 known LED groups unless a key has an explicit color override. Only those overridden keys receive `[FF, j, R, G, B]`. All 14 side-LED overlays are cleared so the global effect controls them. Removing the last override still sends a complete cleared layout. Off ignores saved override colors on the wire and clears every valid overlay entry. It does not delete those colors from the local draft. Empty firmware positions remain five zero bytes.

The application does not expose per-key breathing or independent side-LED colors. Its on-screen preview shows the global base color and static key overrides without animating global effects. A save replaces the complete custom-color overlay; missing entries do not preserve unknown onboard colors.

`GMMKLocation.js:314–336` defines this matrix:

| Row | Left side | Key | Key | Key | Key | Right side |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 0: Side 1 | 1: Num Lock | 2: Divide | 3: Multiply | 4: Subtract | 5: Side 8 |
| 1 | 6: Side 2 | 7: 7 | 8: 8 | 9: 9 | 10: Add | 11: Side 9 |
| 2 | 12: Side 3 | 13: empty | 14: empty | 15: empty | 16: empty | 17: Side 10 |
| 3 | 18: Side 4 | 19: 4 | 20: 5 | 21: 6 | 22: duplicate Add | 23: Side 11 |
| 4 | 24: Side 5 | 25: 1 | 26: 2 | 27: 3 | 28: Enter | 29: Side 12 |
| 5 | 30: Side 6 | 31: empty | 32: empty | 33: empty | 34: empty | 35: Side 13 |
| 6 | 36: Side 7 | 37: empty | 38: 0 | 39: Decimal | 40: empty | 41: Side 14 |

CORE uses the first matching LED index. Add therefore uses position `10`; position `22` remains empty, as do the other holes. There are 31 unique physical LED groups in this 42-position matrix. Knob press (`Mute` in the source) and Slider have no matching LED index. Do not encode negative indices for them.

## Selection, transaction boundaries, and persistence

Command `01` is `07 01 <slot> <layer>` followed by zeros and a 50 ms delay (`GmmkNumpadSeries.js:850–889`). The official UI calls it after changing a key layer or lighting.

`StartHIDWrite` and `EndHIDWrite` at lines `2115–2138` send start/end packets only for Bluetooth. Both are no-ops over USB. No separate USB save/commit command was found. The similarly named `setProfileToDevice` in `Device.js:57–63` only updates the application's local database.

Accordingly, a completed USB transfer is not proof of physical application or durable storage. The initial user has reported good operation, but detailed persistence results have not been recorded. Acceptance requires checking the actual keys/LEDs, disconnecting and reconnecting USB, and checking the same slot/layer again. Use on another computer should be checked independently, with host remapping software disabled for any keys under test.

## Golden frame examples

These are reproducible encodings, not commands to run blindly. All offsets include the report ID. Each expression below produces exactly 264 bytes; `zeros(N)` means N zero bytes.

| Example | Exact bytes |
| --- | --- |
| Status request | `07 8A` + `zeros(262)` |
| Select slot 1, layer 1 | `07 01 01 01` + `zeros(260)` |
| Static red, slot 1/layer 1, speed 100, multicolor off | `07 02 01 01 07 00 00 00 01 00 00 00 00 00 00 FF 00 00` + `zeros(246)` |
| Settings for that example: USB/Bluetooth brightness 100, sensitivity 2, Num Lock indicator off, battery indicator on | `07 07 01 01 07 00 01 00 14 00 08 01 01 14` + `zeros(250)` |

The global example also requires a complete overlay `06` followed by selection `01`. For a cleared overlay at slot 1/layer 1 and brightness 100, the header is `07 06 01 01 2A 00 14 00`; each known LED record is `[00, j, 00, 00, 00]`, all unused records are zero, and the final 46 bytes are zero. It must not be described as preserving a user's prior settings.
