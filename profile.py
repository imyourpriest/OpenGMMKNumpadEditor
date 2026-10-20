"""Portable local profile drafts. No device communication occurs here."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
MAX_PROFILES = 32
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
TARGET = {"vendor_id": "320f", "product_id": "5088", "usb_revision": "0088"}
RGB_EFFECTS = ("static", "breathing", "rainbow", "wave", "reactive", "off")
MODIFIERS = ("ctrl", "shift", "alt", "super")

# Physical positions are independent of any unverified firmware key indices.
# HID Keyboard/Keypad usages below describe intended actions in a local draft.
PHYSICAL_KEYS = [
    {"id": "num_lock", "label": "Num Lock", "row": 0, "col": 0, "width": 1, "height": 1, "default_usage": 0x53},
    {"id": "divide", "label": "/", "row": 0, "col": 1, "width": 1, "height": 1, "default_usage": 0x54},
    {"id": "multiply", "label": "*", "row": 0, "col": 2, "width": 1, "height": 1, "default_usage": 0x55},
    {"id": "subtract", "label": "−", "row": 0, "col": 3, "width": 1, "height": 1, "default_usage": 0x56},
    {"id": "kp_7", "label": "7", "row": 1, "col": 0, "width": 1, "height": 1, "default_usage": 0x5F},
    {"id": "kp_8", "label": "8", "row": 1, "col": 1, "width": 1, "height": 1, "default_usage": 0x60},
    {"id": "kp_9", "label": "9", "row": 1, "col": 2, "width": 1, "height": 1, "default_usage": 0x61},
    {"id": "add", "label": "+", "row": 1, "col": 3, "width": 1, "height": 2, "default_usage": 0x57},
    {"id": "kp_4", "label": "4", "row": 2, "col": 0, "width": 1, "height": 1, "default_usage": 0x5C},
    {"id": "kp_5", "label": "5", "row": 2, "col": 1, "width": 1, "height": 1, "default_usage": 0x5D},
    {"id": "kp_6", "label": "6", "row": 2, "col": 2, "width": 1, "height": 1, "default_usage": 0x5E},
    {"id": "kp_1", "label": "1", "row": 3, "col": 0, "width": 1, "height": 1, "default_usage": 0x59},
    {"id": "kp_2", "label": "2", "row": 3, "col": 1, "width": 1, "height": 1, "default_usage": 0x5A},
    {"id": "kp_3", "label": "3", "row": 3, "col": 2, "width": 1, "height": 1, "default_usage": 0x5B},
    {"id": "enter", "label": "Enter", "row": 3, "col": 3, "width": 1, "height": 2, "default_usage": 0x58},
    {"id": "kp_0", "label": "0", "row": 4, "col": 0, "width": 2, "height": 1, "default_usage": 0x62},
    {"id": "decimal", "label": ".", "row": 4, "col": 2, "width": 1, "height": 1, "default_usage": 0x63},
]
KEY_IDS = frozenset(key["id"] for key in PHYSICAL_KEYS)
_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")


def _default_device_settings(wireless_brightness: int) -> dict[str, Any]:
    return {
        "num_lock_indicator": False,
        "battery_indicator": True,
        "sensitivity": 2,
        "wireless_brightness": wireless_brightness,
    }


def new_document() -> dict[str, Any]:
    """Return three independent local templates, never a device-state claim."""
    profiles = []
    for number in range(1, 4):
        profiles.append({
            "id": f"profile-{number}",
            "name": f"Profile {number}",
            "bindings": {
                key["id"]: {"usage_page": 7, "usage_id": key["default_usage"], "modifiers": []}
                for key in PHYSICAL_KEYS
            },
            "rgb": {
                "effect": "static", "color": "#33aaff", "brightness": 70,
                "speed": 50, "per_key": {},
                "device_settings": _default_device_settings(70),
            },
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "document_kind": "local-draft",
        "target": dict(TARGET),
        "selected_profile_id": profiles[0]["id"],
        "profiles": profiles,
    }


def _object(value: Any, keys: set[str], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be an object")
    if set(value) != keys:
        missing = sorted(keys - set(value))
        extra = sorted(str(key) for key in set(value) - keys)
        raise ValueError(f"{where} has invalid fields (missing: {missing}; unknown: {extra})")
    return value


def _integer(value: Any, minimum: int, maximum: int, where: str) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{where} must be an integer from {minimum} to {maximum}")


def _color(value: Any, where: str) -> None:
    if not isinstance(value, str) or _COLOR.fullmatch(value) is None:
        raise ValueError(f"{where} must be a color in #RRGGBB format")


def _validate_document_version(document: dict[str, Any], version: int) -> None:
    """Validate one exact known structure, including legacy imports before migration."""
    _object(document, {"schema_version", "document_kind", "target", "selected_profile_id", "profiles"}, "Document")
    if type(document["schema_version"]) is not int or document["schema_version"] != version:
        raise ValueError(f"Unsupported schema_version; expected {version}")
    if document["document_kind"] != "local-draft":
        raise ValueError("document_kind must be local-draft")
    _object(document["target"], set(TARGET), "Target")
    if document["target"] != TARGET:
        raise ValueError("This draft must target USB 320f:5088, revision 0088")
    profiles = document["profiles"]
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= MAX_PROFILES:
        raise ValueError(f"profiles must contain 1 to {MAX_PROFILES} local profiles")
    seen_ids: set[str] = set()
    for index, item in enumerate(profiles, 1):
        where = f"Profile {index}"
        _object(item, {"id", "name", "bindings", "rgb"}, where)
        identifier = item["id"]
        if not isinstance(identifier, str) or _ID.fullmatch(identifier) is None:
            raise ValueError(f"{where} id must be 1 to 80 letters, digits, periods, underscores, or hyphens")
        if identifier in seen_ids:
            raise ValueError("Profile ids must be unique")
        seen_ids.add(identifier)
        name = item["name"]
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise ValueError(f"{where} name must contain 1 to 80 printable characters")
        bindings = _object(item["bindings"], set(KEY_IDS), f"{where} bindings")
        for key_id, binding in bindings.items():
            binding_where = f"{where} binding {key_id}"
            _object(binding, {"usage_page", "usage_id", "modifiers"}, binding_where)
            if type(binding["usage_page"]) is not int or binding["usage_page"] != 7:
                raise ValueError(f"{binding_where} usage_page must be 7 (Keyboard/Keypad)")
            _integer(binding["usage_id"], 0, 0xFF, f"{binding_where} usage_id")
            modifiers = binding["modifiers"]
            if not isinstance(modifiers, list) or len(modifiers) > 8:
                raise ValueError(f"{binding_where} modifiers must be a list of up to 8 names")
            if any(not isinstance(modifier, str) or modifier not in MODIFIERS for modifier in modifiers):
                raise ValueError(f"{binding_where} has an unknown modifier")
            if len(set(modifiers)) != len(modifiers):
                raise ValueError(f"{binding_where} modifiers must be unique")
        rgb_fields = {"effect", "color", "brightness", "speed", "per_key"}
        if version == 2:
            rgb_fields.add("device_settings")
        rgb = _object(item["rgb"], rgb_fields, f"{where} RGB")
        if not isinstance(rgb["effect"], str) or rgb["effect"] not in RGB_EFFECTS:
            raise ValueError(f"{where} RGB effect is unknown")
        _color(rgb["color"], f"{where} RGB color")
        _integer(rgb["brightness"], 0, 100, f"{where} RGB brightness")
        _integer(rgb["speed"], 0, 100, f"{where} RGB speed")
        per_key = rgb["per_key"]
        if not isinstance(per_key, dict) or any(key_id not in KEY_IDS for key_id in per_key):
            raise ValueError(f"{where} per_key must map physical key ids to colors")
        for key_id, color in per_key.items():
            _color(color, f"{where} color for {key_id}")
        if version == 2:
            settings_where = f"{where} RGB device_settings"
            settings = _object(rgb["device_settings"], {
                "num_lock_indicator", "battery_indicator", "sensitivity", "wireless_brightness",
            }, settings_where)
            for field in ("num_lock_indicator", "battery_indicator"):
                if type(settings[field]) is not bool:
                    raise ValueError(f"{settings_where} {field} must be a boolean")
            for field in ("sensitivity", "wireless_brightness"):
                _integer(settings[field], 0, 100, f"{settings_where} {field}")
    selected = document["selected_profile_id"]
    if not isinstance(selected, str) or selected not in seen_ids:
        raise ValueError("selected_profile_id must identify an existing profile")


def validate_document(document: dict[str, Any]) -> None:
    """Accept only current schema v2 for saving and device frame building."""
    _validate_document_version(document, SCHEMA_VERSION)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def load_document(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Load strict JSON; migrate valid v1 drafts to v2 without rewriting their file."""
    try:
        with open(path, "rb") as stream:
            content = stream.read(MAX_DOCUMENT_BYTES + 1)
        if len(content) > MAX_DOCUMENT_BYTES:
            raise ValueError("Profile document exceeds the 4 MiB size limit")
        document = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        if isinstance(document, dict) and type(document.get("schema_version")) is int and document["schema_version"] == 1:
            # Validate every legacy field before adding anything. Unknown fields
            # and invalid values must not be hidden or normalized by migration.
            _validate_document_version(document, 1)
            for item in document["profiles"]:
                item["rgb"]["device_settings"] = _default_device_settings(item["rgb"]["brightness"])
            document["schema_version"] = SCHEMA_VERSION
        validate_document(document)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"Invalid profile JSON: {error}") from error
    return document


def save_document(path: str | os.PathLike[str], document: dict[str, Any]) -> None:
    """Replace a local draft atomically, leaving the previous file intact on failure."""
    validate_document(document)
    encoded = (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise ValueError("Profile document exceeds the 4 MiB size limit")
    destination = Path(path)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent, delete=False) as stream:
            temporary_path = stream.name
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
