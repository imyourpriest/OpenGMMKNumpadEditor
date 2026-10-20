"""Narrow Linux hidraw transport for the verified stock Numpad interface.

Only report 07 on the exact vendor configuration interface may be exchanged.
No firmware, bootloader, raw output-report, or capture-replay operation exists.
Constructing a transport does not open a HID node; use it as a context manager.
"""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import hashlib
import os
from pathlib import Path
import platform
import re
import stat
import struct
import sys
from typing import Any, Callable, TypeVar


SYSFS_ROOT = Path("/sys/class/hidraw")
DEV_ROOT = Path("/dev")
VENDOR_ID = 0x320F
PRODUCT_ID = 0x5088
USB_REVISION = "0088"
INTERFACE_NUMBER = "02"
REPORT_ID = 7
REPORT_LENGTH = 264
ALLOWED_COMMANDS = frozenset((0x8A, 0x03, 0x01, 0x02, 0x07, 0x06))
EXPECTED_DESCRIPTOR = bytes.fromhex("0601ff0901a1018507150026ff0009207508960701b102c0")
EXPECTED_DESCRIPTOR_SHA256 = "fda4cd2490bbe43df640a5f6f21ba23a2c586ada787d40d777d637d675015aed"
HID_MAX_DESCRIPTOR_SIZE = 4096

# Linux asm-generic ioctl encoding, checked against the installed hidraw.h and
# architecture headers with a compiled metadata-only program. Architectures
# with different ioctl encoding are rejected before opening a device.
_CHECKED_ARCHITECTURES = frozenset(("x86_64", "amd64", "aarch64", "arm64", "i386", "i486", "i586", "i686", "armv6l", "armv7l", "riscv64"))


def _ioc(direction: int, number: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord("H") << 8) | number


HIDIOCGRDESCSIZE = _ioc(2, 0x01, 4)
HIDIOCGRDESC = _ioc(2, 0x02, 4 + HID_MAX_DESCRIPTOR_SIZE)
HIDIOCGRAWINFO = _ioc(2, 0x03, 8)
HIDIOCSFEATURE = _ioc(3, 0x06, REPORT_LENGTH)
HIDIOCGFEATURE = _ioc(3, 0x07, REPORT_LENGTH)
_NODE_NAME = re.compile(r"hidraw[0-9]+\Z")
_DEV_NUMBER = re.compile(r"([0-9]+):([0-9]+)\Z")
_T = TypeVar("_T")
_RGB_EFFECT_IDS = frozenset((0, 2, 6, 7, 8, 13))
_PHYSICAL_KEY_INDICES = frozenset(range(20)) - {11, 17, 18}
_PHYSICAL_LED_INDICES = frozenset((1, 2, 3, 4, 7, 8, 9, 10, 19, 20, 21, 25, 26, 27, 28, 38, 39))
_VALID_LED_INDICES = frozenset((
    0, 6, 12, 18, 24, 30, 36, 5, 11, 17, 23, 29, 35, 41,
    1, 2, 3, 4, 7, 8, 9, 10, 19, 20, 21, 25, 26, 27, 28, 38, 39,
))


class TransportError(RuntimeError):
    """Device access or a guarded operation failed; the transport is closed."""


class DeviceValidationError(TransportError):
    """The selected node does not match its exact supported identity."""


class UnsupportedCommand(TransportError):
    """A command outside the explicit reviewed allowlist was requested."""


class InvalidReport(TransportError):
    """A feature report is outside the reviewed framing or payload schema."""


def _validate_command_payload(report: bytes) -> None:
    """Reject extensions, arbitrary settings, and malformed fixed-size frames.

    These constraints match the reviewed pure builders in protocol.py. In
    particular, RGB settings cannot carry unreviewed latency/Win Lock values,
    and a keymap cannot replace the stock encoder press or matrix holes.
    """
    command = report[1]
    if command == 0x8A:
        if any(report[2:]):
            raise InvalidReport("The status request must have a zero payload")
        return
    if not 1 <= report[2] <= 3 or not 1 <= report[3] <= 3:
        raise InvalidReport("Onboard profile and layer must each be from 1 to 3")
    if command == 0x01:
        if any(report[4:]):
            raise InvalidReport("Profile selection has unexpected payload bytes")
    elif command == 0x03:
        if any(report[4:8]) or any(report[48:]):
            raise InvalidReport("The keymap report contains unexpected reserved bytes")
        types = report[8:28]
        usages = report[28:48]
        if types[11] != 3 or usages[11] != 0x10:
            raise InvalidReport("The stock encoder-press calculator action must be preserved")
        if any(types[index] or usages[index] for index in (17, 18)):
            raise InvalidReport("Unused key-matrix records must remain zero")
        if any(types[index] != 1 and not 0xE1 <= types[index] <= 0xEF for index in _PHYSICAL_KEY_INDICES):
            raise InvalidReport("Only keyboard usages and the reviewed modifier encoding are allowed")
    elif command == 0x02:
        effect = report[4]
        if (
            effect not in _RGB_EFFECT_IDS or any(report[5:8])
            or not 1 <= report[8] <= 20 or any(report[9:12])
            or report[12] != int(effect == 13) or any(report[13:15])
            or any(report[18:])
        ):
            raise InvalidReport("The RGB effect report is outside the reviewed effect schema")
    elif command == 0x07:
        effect = report[4]
        if (
            effect not in _RGB_EFFECT_IDS or report[5] != 0 or report[6] != 1
            or report[7] not in (0, 1) or report[8] > 20 or report[9] != 0
            or report[10] != 8 or report[11] > 50 or report[12] not in (0, 1)
            or report[13] > 20 or any(report[14:])
            or (effect == 0 and (report[8] != 10 or report[13] != 10))
        ):
            raise InvalidReport("The RGB settings report is outside the reviewed settings schema")
    elif command == 0x06:
        if report[4] != 42 or report[5] != 0 or report[6] > 20 or report[7] != 0 or any(report[218:]):
            raise InvalidReport("The RGB layout report has invalid bounds or reserved bytes")
        for index in range(42):
            record = report[8 + index * 5:13 + index * 5]
            if index in _VALID_LED_INDICES:
                if record[1] != index:
                    raise InvalidReport("Every valid LED requires its correct matrix index")
                if record[0] == 0:
                    if any(record[2:]):
                        raise InvalidReport("Unassigned LED records must contain zero RGB values")
                elif record[0] != 0xFF or index not in _PHYSICAL_LED_INDICES:
                    raise InvalidReport("Only physical keys may carry explicit static RGB overrides")
            elif any(record):
                raise InvalidReport("Unused LED-matrix records must remain zero")


@dataclass(frozen=True)
class _IdentitySnapshot:
    hid_path: Path
    interface_path: Path
    usb_path: Path
    node_filesystem: int
    node_inode: int
    node_device: int


def _text(path: Path) -> str:
    with path.open("r", encoding="ascii") as stream:
        value = stream.read(129)
    if len(value) > 128:
        raise DeviceValidationError(f"Unexpected sysfs value size: {path.name}")
    return value.strip()


def _lineage(hid_path: Path) -> tuple[Path, Path]:
    interface_path: Path | None = None
    usb_path: Path | None = None
    for current in (hid_path, *hid_path.parents):
        if interface_path is None and (current / "bInterfaceNumber").exists():
            interface_path = current
        if usb_path is None and any((current / name).exists() for name in ("idVendor", "idProduct", "bcdDevice")):
            usb_path = current
        if interface_path is not None and usb_path is not None:
            break
    if interface_path is None or usb_path is None or usb_path not in interface_path.parents:
        raise DeviceValidationError("The HID node has no valid physical USB/interface lineage")
    return interface_path, usb_path


class StockTransport:
    """A context-managed, identity-bound interface-02 feature-report transport.

    Identity and descriptor checks run after open and before every feature
    exchange. Access errors, rejected packets, or identity changes close the fd.
    Importing device dictionaries cannot bypass the independent kernel checks.
    """

    def __init__(self, device: dict[str, Any]):
        self._fd: int | None = None
        self._anchor: _IdentitySnapshot | None = None
        if sys.platform != "linux" or platform.machine().lower() not in _CHECKED_ARCHITECTURES:
            raise TransportError("This Linux architecture's hidraw ioctl layout has not been verified")
        if not isinstance(device, dict) or not (
            device.get("supported_identity") is True
            and device.get("vendor") == "320f"
            and device.get("product") == "5088"
            and device.get("revision") == USB_REVISION
        ):
            raise DeviceValidationError("Expected USB 320f:5088, hardware revision 0088")
        interfaces = device.get("interfaces")
        if not isinstance(interfaces, list):
            raise DeviceValidationError("The selected device has no interface list")
        matches = [item for item in interfaces if isinstance(item, dict) and item.get("number") == INTERFACE_NUMBER]
        if len(matches) != 1:
            raise DeviceValidationError("Exactly one vendor configuration interface 02 is required")
        interface = matches[0]
        if interface.get("descriptor_sha256") != EXPECTED_DESCRIPTOR_SHA256:
            raise DeviceValidationError("The selected interface descriptor is not the verified stock descriptor")
        node_value = interface.get("node")
        usb_value = device.get("sysfs_usb")
        if not isinstance(node_value, str) or not isinstance(usb_value, str):
            raise DeviceValidationError("The selected device is missing its node or physical USB path")
        node_path = Path(node_value)
        usb_path = Path(usb_value)
        if not node_path.is_absolute() or _NODE_NAME.fullmatch(node_path.name) is None or node_path != DEV_ROOT / node_path.name:
            raise DeviceValidationError("The node must be a direct /dev/hidrawN device path")
        if not usb_path.is_absolute():
            raise DeviceValidationError("The physical USB path must be absolute")
        # Freeze the selected paths rather than retaining mutable caller data.
        self._node = node_path
        self._class_path = SYSFS_ROOT / node_path.name
        self._selected_usb = usb_path

    @property
    def is_open(self) -> bool:
        return self._fd is not None

    def _guarded(self, operation: Callable[[], _T]) -> _T:
        try:
            return operation()
        except TransportError:
            self.close()
            raise
        except (OSError, ValueError, UnicodeError, struct.error) as error:
            self.close()
            raise TransportError(f"Stock Numpad device access failed: {error}") from error
        except BaseException:
            self.close()
            raise

    def _inspect_sysfs(self) -> _IdentitySnapshot:
        hid_path = (self._class_path / "device").resolve(strict=True)
        if not hid_path.is_dir():
            raise DeviceValidationError("The HID sysfs device disappeared")
        interface_path, usb_path = _lineage(hid_path)
        if usb_path != self._selected_usb.resolve(strict=True):
            raise DeviceValidationError("The HID node now belongs to a different physical USB device")
        if (
            _text(interface_path / "bInterfaceNumber").lower() != INTERFACE_NUMBER
            or _text(usb_path / "idVendor").lower() != "320f"
            or _text(usb_path / "idProduct").lower() != "5088"
            or _text(usb_path / "bcdDevice").lower() != USB_REVISION
        ):
            raise DeviceValidationError("The current USB identity, revision, or interface does not match")
        with (hid_path / "report_descriptor").open("rb") as stream:
            descriptor = stream.read(len(EXPECTED_DESCRIPTOR) + 1)
        if descriptor != EXPECTED_DESCRIPTOR or hashlib.sha256(descriptor).hexdigest() != EXPECTED_DESCRIPTOR_SHA256:
            raise DeviceValidationError("The current sysfs HID descriptor is not the verified stock descriptor")
        dev_number = _DEV_NUMBER.fullmatch(_text(self._class_path / "dev"))
        if dev_number is None:
            raise DeviceValidationError("The hidraw sysfs device number is invalid")
        expected_device = os.makedev(int(dev_number[1]), int(dev_number[2]))
        node_stat = self._node.lstat()
        if not stat.S_ISCHR(node_stat.st_mode):
            raise DeviceValidationError("The HID node is not a character device (symlinks are refused)")
        if node_stat.st_rdev != expected_device:
            raise DeviceValidationError("The HID node's device number does not match sysfs")
        return _IdentitySnapshot(hid_path, interface_path, usb_path, node_stat.st_dev, node_stat.st_ino, expected_device)

    def _require_open(self) -> int:
        if self._fd is None or self._anchor is None:
            raise TransportError("The stock transport is not open; use it as a context manager")
        return self._fd

    def _verify_open(self) -> int:
        fd = self._require_open()
        snapshot = self._inspect_sysfs()
        if snapshot != self._anchor:
            raise DeviceValidationError("The HID node or USB lineage changed after it was selected")
        node_stat = os.fstat(fd)
        if (
            not stat.S_ISCHR(node_stat.st_mode)
            or node_stat.st_rdev != snapshot.node_device
            or node_stat.st_dev != snapshot.node_filesystem
            or node_stat.st_ino != snapshot.node_inode
        ):
            raise DeviceValidationError("The opened HID fd does not match the selected character-device node")
        raw_info = bytearray(8)
        fcntl.ioctl(fd, HIDIOCGRAWINFO, raw_info, True)
        if struct.unpack("=IHH", raw_info) != (3, VENDOR_ID, PRODUCT_ID):
            raise DeviceValidationError("The HID fd is not the expected USB 320f:5088 device")
        descriptor_size = bytearray(4)
        fcntl.ioctl(fd, HIDIOCGRDESCSIZE, descriptor_size, True)
        if struct.unpack("=I", descriptor_size)[0] != len(EXPECTED_DESCRIPTOR):
            raise DeviceValidationError("The opened HID descriptor size does not match")
        descriptor_buffer = bytearray(4 + HID_MAX_DESCRIPTOR_SIZE)
        struct.pack_into("=I", descriptor_buffer, 0, len(EXPECTED_DESCRIPTOR))
        fcntl.ioctl(fd, HIDIOCGRDESC, descriptor_buffer, True)
        returned_size = struct.unpack_from("=I", descriptor_buffer)[0]
        descriptor = bytes(descriptor_buffer[4:4 + len(EXPECTED_DESCRIPTOR)])
        if returned_size != len(EXPECTED_DESCRIPTOR) or descriptor != EXPECTED_DESCRIPTOR or hashlib.sha256(descriptor).hexdigest() != EXPECTED_DESCRIPTOR_SHA256:
            raise DeviceValidationError("The opened HID descriptor does not match the verified stock descriptor")
        # Recheck pathname/sysfs identity after metadata ioctls, before any
        # vendor feature request. An already-open fd remains identity bound.
        if self._inspect_sysfs() != self._anchor:
            raise DeviceValidationError("The HID path or USB identity changed during validation")
        return fd

    def __enter__(self) -> "StockTransport":
        def enter() -> "StockTransport":
            if self._fd is not None:
                raise TransportError("The stock transport is already open")
            self._anchor = self._inspect_sysfs()
            flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
            self._fd = os.open(self._node, flags)
            # Cooperating editor instances must not interleave a status query
            # and response or a multi-frame update. Interface 01 is untouched.
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._verify_open()
            return self
        return self._guarded(enter)

    def send_feature(self, report: bytes) -> None:
        """Send one fixed-size, explicitly allowed feature report."""
        def send() -> None:
            if not isinstance(report, (bytes, bytearray, memoryview)):
                raise InvalidReport("A feature report must be bytes")
            frozen_report = bytes(report)
            if len(frozen_report) != REPORT_LENGTH or frozen_report[0] != REPORT_ID:
                raise InvalidReport("Expected exactly 264 bytes beginning with report ID 07")
            command = frozen_report[1]
            if command not in ALLOWED_COMMANDS:
                raise UnsupportedCommand(f"Stock feature command 0x{command:02x} is not allowed")
            _validate_command_payload(frozen_report)
            fd = self._verify_open()
            # A mutable buffer preserves the kernel's transferred-byte count.
            result = fcntl.ioctl(fd, HIDIOCSFEATURE, bytearray(frozen_report), True)
            if type(result) is not int or result != REPORT_LENGTH:
                raise TransportError(f"Incomplete feature write: expected 264 bytes, received {result!r}")
        self._guarded(send)

    def get_feature(self) -> bytes:
        """Read only the verified report ID 07, returning its complete 264 bytes."""
        def receive() -> bytes:
            fd = self._verify_open()
            report = bytearray(REPORT_LENGTH)
            report[0] = REPORT_ID
            result = fcntl.ioctl(fd, HIDIOCGFEATURE, report, True)
            if type(result) is not int or result != REPORT_LENGTH:
                raise TransportError(f"Incomplete feature read: expected 264 bytes, received {result!r}")
            if len(report) != REPORT_LENGTH or report[0] != REPORT_ID:
                raise InvalidReport("The feature response has the wrong report ID or length")
            return bytes(report)
        return self._guarded(receive)

    def close(self) -> None:
        fd, self._fd = self._fd, None
        self._anchor = None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()
