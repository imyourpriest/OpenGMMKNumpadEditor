"""Pure encoders for the verified stock GMMK Numpad USB protocol.

No device is opened and no report is sent here. Protocol facts were independently
derived from official Legacy CORE 1.1.36, extracted without running it.
Reference: src/electron-process/renderer-interop/protocol/device/keyboard/
GmmkNumpadSeries.js, SHA-256
8820d9b28a279dcb5d5db5e0e6399d50aa9b584164c4b9d04729f75a17c1dffe.
Key-table layout/defaults: others/GMMKLocation.js lines 305-339, SHA-256
724a5c4d0832af3570479cf39bb4728c8fdbb0a0d9281a55b7de17d7321a1417.
Key usage mapping: others/SupportData.js, SHA-256
0b72a0b6d93e13746489a0fab7e6a272599f8f4ae50af69a2c7870595cc42f92.
Key encoding: Numpad source 1336-1390, 1486-1519; selection: 850-885;
status: 2350-2377; RGB: 24-45, 1753-1860. Native HIDAPI framing:
hiddevice/src/hiddevice.cc 565-650. No vendor implementation is bundled.

A keymap report replaces one complete layer. Existing onboard profiles cannot
be read with any command evidenced here. Power-cycle persistence requires a
live test; no separate USB commit command was found.
"""

from __future__ import annotations

from typing import Any

import profile as profile_model


REPORT_ID = 7
REPORT_BYTES = 264
FEATURE_DESCRIPTOR_EVIDENCE = {
    "vendor": "320f",
    "product": "5088",
    "revision": "0088",
    "interface": "02",
    "report_type": "feature",
    "report_id": REPORT_ID,
    "report_size_bits": 8,
    "report_count": 263,
    "payload_bytes": 263,
    "total_bytes": REPORT_BYTES,
    "descriptor_sha256": "fda4cd2490bbe43df640a5f6f21ba23a2c586ada787d40d777d637d675015aed",
}

# Stock 5-by-4 matrix: 17/18 are holes; 11 is the encoder press.
KEY_INDICES = {
    "num_lock": 0, "divide": 1, "multiply": 2, "subtract": 3,
    "kp_7": 4, "kp_8": 5, "kp_9": 6, "add": 7,
    "kp_4": 8, "kp_5": 9, "kp_6": 10,
    "kp_1": 12, "kp_2": 13, "kp_3": 14, "enter": 15,
    "kp_0": 16, "decimal": 19,
}
DEFAULT_KEY_TABLE = bytes.fromhex(
    "01 01 01 01 01 01 01 01 01 01 01 03 01 01 01 01 01 00 00 01 "
    "53 54 55 56 5f 60 61 57 5c 5d 5e 10 59 5a 5b 58 62 00 00 63"
)
MODIFIER_BITS = {"ctrl": 1, "shift": 2, "alt": 4, "super": 8}
RGB_EFFECT_IDS = {"static": 7, "breathing": 6, "rainbow": 13, "wave": 2, "reactive": 8, "off": 0}
KEY_LED_INDICES = {
    "num_lock": 1, "divide": 2, "multiply": 3, "subtract": 4,
    "kp_7": 7, "kp_8": 8, "kp_9": 9, "add": 10,
    "kp_4": 19, "kp_5": 20, "kp_6": 21,
    "kp_1": 25, "kp_2": 26, "kp_3": 27, "enter": 28,
    "kp_0": 38, "decimal": 39,
}
SIDE_LED_INDICES = (0, 6, 12, 18, 24, 30, 36, 5, 11, 17, 23, 29, 35, 41)


class ProtocolUnavailable(RuntimeError):
    """The verified protocol required for this operation is not available."""

def _integer(value: Any, minimum: int, maximum: int, name: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def _report(command: int, slot: int | None = None, layer: int | None = None) -> bytearray:
    data = bytearray(REPORT_BYTES)
    data[0:2] = bytes((REPORT_ID, command))
    if slot is not None:
        data[2] = _integer(slot, 1, 3, "slot")
    if layer is not None:
        data[3] = _integer(layer, 1, 3, "layer")
    return data


def build_status_request() -> bytes:
    """Build the documented battery/firmware query."""
    return bytes(_report(0x8A))


def parse_status(raw: bytes | bytearray) -> dict[str, Any]:
    """Parse raw HIDAPI bytes including report ID; retain raw charging value.

    Firmware is represented as four hexadecimal digits without assuming BCD.
    The source does not document the charging enumeration.
    """
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != REPORT_BYTES:
        raise ValueError("Status response must contain exactly 264 bytes including report ID")
    if raw[0] != REPORT_ID:
        raise ValueError("Status response must use report ID 07")
    if raw[2] > 100:
        raise ValueError("Status response battery must be from 0 to 100")
    firmware_word = raw[4] | (raw[5] << 8)
    return {
        "response_code": raw[1], "battery": raw[2], "charging": raw[3],
        "firmware_word": firmware_word,
        "firmware_version": f"{firmware_word:04x}",
    }


def _validate_profile(item: dict[str, Any]) -> None:
    if not isinstance(item, dict):
        raise ValueError("Profile must be an object")
    profile_model.validate_document({
        "schema_version": profile_model.SCHEMA_VERSION,
        "document_kind": "local-draft", "target": dict(profile_model.TARGET),
        "selected_profile_id": item.get("id"), "profiles": [item],
    })


def build_keymap(profile_dict: dict[str, Any], slot: int = 1, layer: int = 1) -> bytes:
    """Build one complete layer, with 17 keys and generic modifier combinations.

    Encoder press retains its stock calculator action. Slider and matrix holes
    are not configurable here. This factory-based table is not an onboard backup.
    """
    _validate_profile(profile_dict)
    data = _report(0x03, slot, layer)
    table = bytearray(DEFAULT_KEY_TABLE)
    for key_id, index in KEY_INDICES.items():
        binding = profile_dict["bindings"][key_id]
        usage = _integer(binding["usage_id"], 0, 255, f"{key_id} usage_id")
        mask = 0
        for modifier in binding["modifiers"]:
            if modifier not in MODIFIER_BITS:
                raise ValueError(f"Unsupported stock modifier: {modifier}")
            mask |= MODIFIER_BITS[modifier]
        table[index] = 0xE0 + mask if mask else 0x01
        table[20 + index] = usage
    data[8:48] = table
    return bytes(data)


def build_select_profile(slot: int = 1, layer: int = 1) -> bytes:
    """Select one of three onboard slots and three layers."""
    return bytes(_report(0x01, slot, layer))


def _validate_rgb(rgb: dict[str, Any]) -> None:
    item = profile_model.new_document()["profiles"][0]
    item["rgb"] = rgb
    _validate_profile(item)


def build_rgb_effect(rgb: dict[str, Any], slot: int = 1, layer: int = 1) -> bytes:
    """Build one RGB effect report; a settings report is also needed for brightness.

    Local 'wave' means stock Wave #1 and 'reactive' means stock Ripple Graff.
    Multicolor is disabled, as in the source's initial effect parameters.
    """
    _validate_rgb(rgb)
    data = _report(0x02, slot, layer)
    data[4] = RGB_EFFECT_IDS[rgb["effect"]]
    data[8] = (2000 - rgb["speed"] * 19) // 100
    data[12] = int(data[4] == 13)
    data[15:18] = bytes.fromhex(rgb["color"][1:])
    return bytes(data)


def build_rgb_settings(
    rgb: dict[str, Any], slot: int = 1, layer: int = 1, *,
    num_lock_indicator: bool, battery_indicator: bool,
    sensitivity: int, wireless_brightness: int,
) -> bytes:
    """Build stock RGB settings, with all additional choices explicit.

    The official frame also sets Win Lock=0, per-key enable=1, and latency=8.
    It cannot preserve unknown onboard values for those fields. Indicator and
    sensitivity values must come from an explicit configuration, not status.
    """
    _validate_rgb(rgb)
    if type(num_lock_indicator) is not bool or type(battery_indicator) is not bool:
        raise ValueError("RGB indicator choices must be booleans")
    _integer(sensitivity, 0, 100, "sensitivity")
    _integer(wireless_brightness, 0, 100, "wireless_brightness")
    data = _report(0x07, slot, layer)
    data[4] = RGB_EFFECT_IDS[rgb["effect"]]
    data[6] = 1
    data[7] = int(num_lock_indicator)
    data[8] = rgb["brightness"] // 5
    data[10] = 8
    data[11] = sensitivity // 2
    data[12] = int(battery_indicator)
    data[13] = wireless_brightness // 5
    if data[4] == 0:
        data[8] = data[13] = 10
    return bytes(data)


def build_per_key_rgb(rgb: dict[str, Any], slot: int = 1, layer: int = 1) -> bytes:
    """Clear the complete per-key layout, then apply explicit key overrides.

    Global base colors and animations come from effect report 02. Every valid
    key/side LED receives visibility 00, its index, and zero color unless that
    physical key has an explicit static override (visibility FF). Effect 'off'
    suppresses every override. Sending this report even with no overrides clears
    earlier onboard palettes. Eleven unused matrix records and the final 46
    bytes remain zero. Encoder and slider have no LED position.
    Sources: Numpad.js 1889-2058; Numpad layout defaults and PERKEYErase behavior.
    """
    _validate_rgb(rgb)
    data = _report(0x06, slot, layer)
    data[4] = 42
    data[6] = rgb["brightness"] // 5
    for index in set(SIDE_LED_INDICES) | set(KEY_LED_INDICES.values()):
        data[8 + index * 5 + 1] = index
    if rgb["effect"] == "off":
        return bytes(data)
    for key_id, color_string in rgb["per_key"].items():
        index = KEY_LED_INDICES[key_id]
        color = bytes.fromhex(color_string[1:])
        start = 8 + index * 5
        data[start:start + 5] = bytes((0xFF, index)) + color
    return bytes(data)


def configuration_interface(device: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return one accessible, descriptor-matched configuration interface.

    Checks metadata only. A transport must recheck the live device before using
    it. Imported profile documents must never supply device metadata.
    """
    if not isinstance(device, dict) or not (
        device.get("supported_identity") is True
        and device.get("vendor") == "320f"
        and device.get("product") == "5088"
        and device.get("revision") == "0088"
    ):
        return None
    interfaces = device.get("interfaces")
    if not isinstance(interfaces, list):
        return None
    candidates = []
    for interface in interfaces:
        if not isinstance(interface, dict) or not (
            interface.get("number") == "02"
            and interface.get("descriptor_sha256") == FEATURE_DESCRIPTOR_EVIDENCE["descriptor_sha256"]
            and interface.get("node_available") is True
            and interface.get("node_is_character") is True
            and interface.get("readable") is True
            and interface.get("writable") is True
            and isinstance(interface.get("node"), str)
            and interface["node"].startswith("/dev/hidraw")
        ):
            continue
        reports = interface.get("reports")
        if isinstance(reports, list) and any(isinstance(report, dict) and (
            report.get("report_type") == "feature"
            and report.get("report_id") == REPORT_ID
            and report.get("data_bits") == 263 * 8
            and report.get("data_bytes") == 263
            and report.get("total_bytes") == REPORT_BYTES
        ) for report in reports):
            candidates.append(interface)
    return candidates[0] if len(candidates) == 1 else None


def capabilities(device: dict[str, Any] | None) -> dict[str, Any]:
    """Report descriptor-verified capabilities, never imported profile claims."""
    interface = configuration_interface(device)
    if interface is None:
        reason = (
            "Select one supported USB 320f:5088 revision 0088 with an accessible "
            "configuration interface 02 and its verified feature-report descriptor."
        )
    else:
        reason = (
            "Stock USB commands are verified. Saving replaces a complete selected "
            "key layer; existing onboard profiles cannot be read or backed up."
        )
    return {
        "can_read_device_status": interface is not None,
        "can_read_device_profiles": False,
        "can_write_device": interface is not None, "reason": reason,
    }


def apply_to_device(document: dict[str, Any], device: dict[str, Any] | None) -> None:
    """Pure builders cannot perform writes; use a reviewed transport."""
    raise ProtocolUnavailable("This module only builds reports; no HID transport is available here.")
