"""Inspect USB identity and HID descriptors using sysfs only.

No hidraw node is opened. Descriptor metadata is not a configuration protocol.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any


VENDOR_ID = "320f"
PRODUCT_ID = "5088"
USB_REVISION = "0088"
MAX_DESCRIPTOR_BYTES = 65536


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None


def _hex(value: str | None) -> str:
    return "" if value is None else value.strip().lower().removeprefix("0x")


def _lineage(device_path: Path) -> tuple[Path, Path] | None:
    """Find the nearest USB interface and physical USB parent independently."""
    interface: Path | None = None
    usb: Path | None = None
    for current in (device_path, *device_path.parents):
        if interface is None and _text(current / "bInterfaceNumber") is not None:
            interface = current
        if usb is None and any((current / field).exists() for field in ("idVendor", "idProduct", "bcdDevice")):
            usb = current
        if interface is not None and usb is not None:
            break
    if interface is None or usb is None or interface == usb or usb not in interface.parents:
        return None
    return interface, usb


def parse_report_descriptor(descriptor: bytes) -> list[dict[str, Any]]:
    """Summarize declared report sizes without communicating with the device.

    Size includes the report-ID byte only when a nonzero report ID is declared.
    Malformed descriptors raise ValueError; unknown items are skipped.
    """
    if not isinstance(descriptor, bytes) or len(descriptor) > MAX_DESCRIPTOR_BYTES:
        raise ValueError("HID descriptor must be bytes, at most 64 KiB")
    offset = 0
    globals_state = {"size": 0, "count": 0, "id": 0}
    stack: list[dict[str, int]] = []
    totals: dict[tuple[str, int], int] = {}
    while offset < len(descriptor):
        prefix = descriptor[offset]
        offset += 1
        if prefix == 0xFE:
            if offset + 2 > len(descriptor):
                raise ValueError("Truncated HID long item")
            length = descriptor[offset]
            offset += 2
            if offset + length > len(descriptor):
                raise ValueError("Truncated HID long-item data")
            offset += length
            continue
        item_size = (0, 1, 2, 4)[prefix & 3]
        item_type = (prefix >> 2) & 3
        tag = prefix >> 4
        if offset + item_size > len(descriptor):
            raise ValueError("Truncated HID short item")
        value = int.from_bytes(descriptor[offset:offset + item_size], "little")
        offset += item_size
        if item_type == 1:
            if tag == 7:
                globals_state["size"] = value
            elif tag == 8:
                if not 1 <= value <= 255:
                    raise ValueError("Invalid HID report ID")
                globals_state["id"] = value
            elif tag == 9:
                globals_state["count"] = value
            elif tag == 10:
                stack.append(dict(globals_state))
            elif tag == 11:
                if not stack:
                    raise ValueError("HID global Pop has no matching Push")
                globals_state = stack.pop()
        elif item_type == 0 and tag in (8, 9, 11):
            kind = {8: "input", 9: "output", 11: "feature"}[tag]
            key = (kind, globals_state["id"])
            bits = globals_state["size"] * globals_state["count"]
            totals[key] = totals.get(key, 0) + bits
            if totals[key] > 16 * 1024 * 1024:
                raise ValueError("Implausibly large HID report")
    return [{
        "report_type": kind,
        "report_id": report_id,
        "data_bits": bits,
        "data_bytes": (bits + 7) // 8,
        "total_bytes": (bits + 7) // 8 + int(report_id != 0),
    } for (kind, report_id), bits in totals.items()]


def scan_devices(sysfs_root: str = "/sys/class/hidraw", dev_root: str = "/dev") -> list[dict[str, Any]]:
    """Group matching model interfaces by their physical USB parent.

    A different hardware revision remains visible but is not a supported identity.
    Node availability/access describes this process's view, including its sandbox.
    """
    grouped: dict[str, dict[str, Any]] = {}
    try:
        entries = sorted(Path(sysfs_root).glob("hidraw*"), key=lambda path: path.name)
    except OSError:
        return []
    for entry in entries:
        try:
            device_path = (entry / "device").resolve(strict=True)
            lineage = _lineage(device_path)
            if lineage is None:
                continue
            interface_path, usb_path = lineage
            vendor = _hex(_text(usb_path / "idVendor"))
            product = _hex(_text(usb_path / "idProduct"))
            revision = _hex(_text(usb_path / "bcdDevice"))
            number = _hex(_text(interface_path / "bInterfaceNumber"))
            if vendor != VENDOR_ID or product != PRODUCT_ID or not number:
                continue
            physical_id = str(usb_path)
            if physical_id not in grouped:
                grouped[physical_id] = {
                    "vendor": vendor,
                    "product": product,
                    "revision": revision,
                    "sysfs_usb": physical_id,
                    "manufacturer": _text(usb_path / "manufacturer") or "",
                    "product_name": _text(usb_path / "product") or "GMMK Numpad",
                    "interfaces": [],
                    "supported_identity": revision == USB_REVISION,
                }
            node = Path(dev_root) / entry.name
            node_available = node.exists()
            try:
                node_is_character = stat.S_ISCHR(node.stat().st_mode)
            except OSError:
                node_is_character = False
            interface: dict[str, Any] = {
                "number": number,
                "node": str(node),
                "descriptor_sha256": "",
                "report_descriptor_hex": "",
                "node_available": node_available,
                "readable": node_available and os.access(node, os.R_OK),
                "writable": node_available and os.access(node, os.W_OK),
                "node_is_character": node_is_character,
                "reports": [],
            }
            try:
                # This is a kernel-exported descriptor file, never a hidraw node.
                with (device_path / "report_descriptor").open("rb") as stream:
                    descriptor = stream.read(MAX_DESCRIPTOR_BYTES + 1)
                if len(descriptor) > MAX_DESCRIPTOR_BYTES:
                    raise ValueError("HID descriptor exceeds 64 KiB")
                interface["report_descriptor_hex"] = descriptor.hex()
                interface["descriptor_sha256"] = hashlib.sha256(descriptor).hexdigest()
                interface["reports"] = parse_report_descriptor(descriptor)
            except (OSError, ValueError) as error:
                interface["descriptor_error"] = str(error)
            grouped[physical_id]["interfaces"].append(interface)
        except (OSError, RuntimeError, ValueError):
            # Hotplug or an unreadable sysfs entry should not break other devices.
            continue
    result = sorted(grouped.values(), key=lambda item: item["sysfs_usb"])
    for device in result:
        device["interfaces"].sort(key=lambda item: (item["number"], item["node"]))
    return result


def select_device(devices: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return one exact matching physical device, refusing ambiguous matches."""
    matches = [device for device in devices if (
        device.get("supported_identity") is True
        and device.get("vendor") == VENDOR_ID
        and device.get("product") == PRODUCT_ID
        and device.get("revision") == USB_REVISION
    )]
    return matches[0] if len(matches) == 1 else None
