"""Low-impact, read-only connection and battery monitoring.

``MonitorCoordinator.poll`` belongs on a worker thread. It never pairs or
connects Bluetooth devices and sends no Bluetooth vendor commands. USB battery
reads use only the existing, reviewed 0x8a status operation. The transport's
nonblocking device lock keeps monitoring out of configuration transactions.

BlueZ API: https://bluez.readthedocs.io/en/latest/device-api/
           https://bluez.readthedocs.io/en/latest/battery-api/
UPower API: https://upower.freedesktop.org/docs/Device.html
The latter is accepted only when NativePath identifies the selected BlueZ
device exactly; model names alone never associate a battery with a numpad.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
import threading
import time
from typing import Any, Callable

from device import scan_devices, select_device


CONNECTION_INTERVAL = 5.0
USB_BATTERY_INTERVAL = 30.0
BATTERY_TTL = 120.0
DBUS_TIMEOUT_MS = 1000
BLUEZ_DEVICE = "org.bluez.Device1"
BLUEZ_BATTERY = "org.bluez.Battery1"
UPOWER_DEVICE = "org.freedesktop.UPower.Device"
_MODALIAS = re.compile(r"usb:v([0-9a-f]{4})p([0-9a-f]{4})d[0-9a-f]{4}\Z", re.I)
_BLUEZ_PATH = re.compile(r"/org/bluez/hci[0-9]+/dev_([0-9A-F]{2}(?:_[0-9A-F]{2}){5})\Z", re.I)
_KNOWN_IDS = frozenset((("320f", "5088"), ("093a", "2860")))
_KNOWN_NAMES = frozenset(("gmmk numpad", "glorious gmmk numpad"))


@dataclass(frozen=True)
class MonitorSnapshot:
    """Public state contains no Bluetooth address or full device inventory.

    USB wins the display when both connections are present; this describes
    connectivity, not which route is currently delivering key events.
    ``timestamp`` is the wall-clock time of the last good battery reading.
    A retained reading becomes stale on a failed refresh, and expires at TTL.
    """

    transport: str = "disconnected"
    usb_connected: bool = False
    bluetooth_connected: bool = False
    battery_percent: int | None = None
    charging: bool | None = None
    firmware: str | None = None
    source: str | None = None
    timestamp: float = 0.0
    stale: bool = False
    error: str | None = None

    @property
    def connection(self) -> str:
        return self.transport

    @property
    def updated_at(self) -> float:
        return self.timestamp


@dataclass(frozen=True)
class BluetoothObservation:
    """Private resolver result; path is used only as an in-memory identity."""

    connected: bool = False
    ambiguous: bool = False
    identity_verified: bool = False
    path: str | None = None
    battery_percent: int | None = None
    charging: bool | None = None
    source: str | None = None
    error: str | None = None


def _percentage(value: Any, *, fractional: bool = False) -> int | None:
    if type(value) is int and 0 <= value <= 100:
        return value
    if fractional and type(value) is float and math.isfinite(value) and 0 <= value <= 100:
        return int(value + 0.5)
    return None


def _target_identity(properties: dict[str, Any]) -> str | None:
    modalias = properties.get("Modalias")
    if isinstance(modalias, str) and modalias:
        match = _MODALIAS.fullmatch(modalias)
        if match and (match[1].lower(), match[2].lower()) in _KNOWN_IDS:
            return "verified"
        # A name cannot override a conflicting vendor/product identity.
        return None
    name = properties.get("Name")
    if isinstance(name, str) and " ".join(name.casefold().split()) in _KNOWN_NAMES:
        return "name-only"
    return None


def bluetooth_from_objects(objects: dict[str, Any]) -> BluetoothObservation:
    """Resolve one connected paired target from BlueZ ObjectManager data.

    A unique exact name without a Modalias supports connection display only.
    A user-editable Alias never identifies hardware. Multiple connected target
    candidates fail closed, and no battery is taken from a different object.
    """
    if not isinstance(objects, dict):
        return BluetoothObservation(error="Bluetooth status is unavailable.")
    candidates = []
    for path, interfaces in objects.items():
        if not isinstance(path, str) or _BLUEZ_PATH.fullmatch(path) is None or not isinstance(interfaces, dict):
            continue
        properties = interfaces.get(BLUEZ_DEVICE)
        if not isinstance(properties, dict):
            continue
        identity = _target_identity(properties)
        if identity and properties.get("Paired") is True and properties.get("Connected") is True:
            candidates.append((path, interfaces, identity))
    if len(candidates) > 1:
        return BluetoothObservation(connected=True, ambiguous=True,
                                    error="More than one numpad is connected over Bluetooth.")
    if not candidates:
        return BluetoothObservation()
    path, interfaces, identity = candidates[0]
    if identity != "verified":
        return BluetoothObservation(connected=True, path=path,
                                    error="Bluetooth found by name; battery identity is unverified.")
    properties = interfaces.get(BLUEZ_BATTERY, {})
    percent = _percentage(properties.get("Percentage")) if isinstance(properties, dict) else None
    return BluetoothObservation(connected=True, identity_verified=True, path=path,
                                battery_percent=percent,
                                source="bluez" if percent is not None else None)


def upower_battery_for_device(devices: list[dict[str, Any]], bluez_path: str) -> tuple[int | None, bool | None]:
    """Find an exact NativePath association, refusing duplicate battery matches.

    BlueZ-backed UPower versions can use the BlueZ object path or its complete
    Bluetooth address as NativePath. Matching is exact, never a substring of a
    model, serial, UPower object path, or an unrelated laptop/mouse battery.
    """
    match = _BLUEZ_PATH.fullmatch(bluez_path) if isinstance(bluez_path, str) else None
    if match is None:
        return None, None
    native_paths = {bluez_path.casefold(), match[1].replace("_", ":").casefold()}
    matches = [properties for properties in devices if (
        isinstance(properties, dict)
        and isinstance(properties.get("NativePath"), str)
        and properties["NativePath"].casefold() in native_paths
        and properties.get("IsPresent") is True
        and properties.get("PowerSupply") is not True
    )]
    if len(matches) != 1:
        return None, None
    properties = matches[0]
    # Coarse levels are not presented as a fabricated precise percentage.
    if properties.get("BatteryLevel", 1) != 1:
        return None, None
    percent = _percentage(properties.get("Percentage"), fractional=True)
    if percent is None:
        return None, None
    state = properties.get("State")
    charging = True if type(state) is int and state == 1 else (
        False if type(state) is int and state in (2, 3, 4) else None)
    return percent, charging


class SystemBluetoothProvider:
    """Lazy optional Gio reader; all D-Bus calls are read-only and bounded.

    No discovery, pairing, connection, GATT reads, Refresh, or property writes
    are performed. Services are not auto-started. A missing daemon or Python GI
    support simply leaves Bluetooth information unavailable.
    """

    def __init__(self):
        self._connection = None

    def _call(self, service, path, interface, method, signature, args, reply_type, *, timeout_ms=DBUS_TIMEOUT_MS):
        from gi.repository import Gio, GLib
        if self._connection is None or self._connection.is_closed():
            self._connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        result = self._connection.call_sync(
            service, path, interface, method,
            GLib.Variant(signature, args) if signature else None,
            GLib.VariantType.new(reply_type), Gio.DBusCallFlags.NO_AUTO_START,
            max(1, min(DBUS_TIMEOUT_MS, timeout_ms)), None)
        return result.unpack()[0]

    def read(self) -> BluetoothObservation:
        try:
            objects = self._call("org.bluez", "/", "org.freedesktop.DBus.ObjectManager",
                                 "GetManagedObjects", None, None, "(a{oa{sa{sv}}})")
        except Exception:
            self._connection = None
            # Keep addresses, remote names, and daemon errors out of the UI.
            return BluetoothObservation(error="Bluetooth status is unavailable.")
        observation = bluetooth_from_objects(objects)
        if (not observation.connected or observation.ambiguous
                or not observation.identity_verified or observation.battery_percent is not None):
            return observation
        # Only consult UPower for a verified, uniquely selected connected target.
        try:
            deadline = time.monotonic() + 1.0
            paths = self._call("org.freedesktop.UPower", "/org/freedesktop/UPower",
                               "org.freedesktop.UPower", "EnumerateDevices", None, None, "(ao)")
            if not isinstance(paths, (list, tuple)) or len(paths) > 32:
                return observation
            batteries = []
            for path in paths:
                remaining_ms = int((deadline - time.monotonic()) * 1000)
                if remaining_ms <= 0:
                    return observation
                if not isinstance(path, str) or not path.startswith("/org/freedesktop/UPower/devices/"):
                    continue
                properties = self._call("org.freedesktop.UPower", path,
                    "org.freedesktop.DBus.Properties", "GetAll", "(s)", (UPOWER_DEVICE,), "(a{sv})",
                    timeout_ms=remaining_ms)
                if isinstance(properties, dict):
                    batteries.append(properties)
            percent, charging = upower_battery_for_device(batteries, observation.path)
        except Exception:
            return observation
        if percent is None:
            return observation
        return BluetoothObservation(connected=True, identity_verified=True, path=observation.path,
                                    battery_percent=percent, charging=charging, source="upower")


def _read_usb_status(device):
    # Delay importing the HID backend too, so pure monitoring models remain
    # usable on CI platforms without Linux fcntl/hidraw support.
    import backend
    return backend.read_status(device=device)


class MonitorCoordinator:
    """Rate-limited synchronous polling with a fast, thread-safe cached view.

    The caller runs ``poll`` in a worker and delivers its immutable snapshot to
    GTK. Overlapping polls return the previous snapshot immediately. A failed
    or busy device read retains the same device's last good reading for 120 s.
    Connecting a different device or changing transports clears that cache.
    """

    def __init__(self, *, scan_usb: Callable | None = None, read_usb: Callable | None = None,
                 bluetooth: Any = None, monotonic: Callable = time.monotonic,
                 wall_clock: Callable = time.time, connection_interval=CONNECTION_INTERVAL,
                 battery_interval=USB_BATTERY_INTERVAL, battery_ttl=BATTERY_TTL):
        self._scan_usb = scan_usb or scan_devices
        self._read_usb = read_usb
        self._bluetooth = bluetooth if bluetooth is not None else SystemBluetoothProvider()
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._connection_interval = max(1.0, float(connection_interval))
        self._battery_interval = max(self._connection_interval, float(battery_interval))
        self._battery_ttl = max(self._battery_interval, float(battery_ttl))
        self._poll_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._snapshot = MonitorSnapshot()
        self._last_connection = float("-inf")
        self._last_battery_attempt = float("-inf")
        self._last_good = float("-inf")
        self._identity = None
        self._battery = None
        self._firmware = None
        self._charging = None
        self._source = None
        self._timestamp = 0.0
        self._read_error = None

    def get_snapshot(self) -> MonitorSnapshot:
        with self._state_lock:
            return self._snapshot

    def _publish(self, snapshot):
        with self._state_lock:
            self._snapshot = snapshot
        return snapshot

    def poll(self, force: bool = False) -> MonitorSnapshot:
        if not self._poll_lock.acquire(blocking=False):
            return self.get_snapshot()
        try:
            now = self._monotonic()
            if not force and now - self._last_connection < self._connection_interval:
                return self.get_snapshot()
            self._last_connection = now
            try:
                devices = self._scan_usb()
                selected = select_device(devices)
                count = sum(1 for device in devices if select_device([device]) is not None)
                usb_error = None
            except Exception:
                devices, selected, count = [], None, 0
                usb_error = "USB connection status is unavailable."
            try:
                bluetooth = self._bluetooth.read()
                if not isinstance(bluetooth, BluetoothObservation):
                    raise ValueError("Invalid Bluetooth observation")
            except Exception:
                bluetooth = BluetoothObservation(error="Bluetooth status is unavailable.")
            usb_connected = count > 0
            bt_connected = bluetooth.connected
            if count > 1:
                transport, identity = "ambiguous", None
                error = "More than one supported numpad is connected by USB."
            elif selected is not None:
                transport, identity = "usb", ("usb", selected.get("sysfs_usb"))
                error = None
            elif bluetooth.ambiguous:
                transport, identity = "ambiguous", None
                error = bluetooth.error
            elif bluetooth.connected:
                transport, identity = "bluetooth", ("bluetooth", bluetooth.path)
                error = bluetooth.error
            else:
                transport, identity = "disconnected", None
                error = usb_error or bluetooth.error
            if identity != self._identity:
                self._identity = identity
                self._battery = self._firmware = self._charging = self._source = None
                self._timestamp = 0.0
                self._last_good = self._last_battery_attempt = float("-inf")
                self._read_error = None
            if transport == "usb":
                if force or now - self._last_battery_attempt >= self._battery_interval:
                    self._last_battery_attempt = now
                    try:
                        status = self._read_usb() if self._read_usb is not None else _read_usb_status(selected)
                        percent = _percentage(status.get("battery"))
                        if percent is None:
                            raise ValueError("Invalid battery percentage")
                        self._battery = percent
                        # The manufacturer's USB charging enumeration has not
                        # been documented: cable presence is not charging proof.
                        self._charging = None
                        firmware = status.get("firmware_version")
                        self._firmware = firmware if isinstance(firmware, str) else None
                        self._source = "usb"
                        self._timestamp = self._wall_clock()
                        self._last_good = now
                        self._read_error = None
                    except Exception:
                        self._read_error = "Battery unavailable; the numpad may be busy or USB access may need setup."
            elif transport == "bluetooth" and bluetooth.identity_verified:
                percent = _percentage(bluetooth.battery_percent)
                if percent is not None:
                    self._battery, self._charging = percent, bluetooth.charging
                    self._source = bluetooth.source
                    self._timestamp, self._last_good = self._wall_clock(), now
                    self._read_error = None
                else:
                    self._read_error = "Bluetooth does not currently report a battery percentage."
            fresh_enough = identity is not None and now - self._last_good < self._battery_ttl
            battery = self._battery if fresh_enough else None
            return self._publish(MonitorSnapshot(
                transport=transport, usb_connected=usb_connected, bluetooth_connected=bt_connected,
                battery_percent=battery, charging=self._charging if fresh_enough else None,
                firmware=self._firmware if fresh_enough else None,
                source=self._source if fresh_enough else None, timestamp=self._timestamp,
                stale=self._battery is not None and (self._read_error is not None or not fresh_enough),
                error=error or self._read_error))
        finally:
            self._poll_lock.release()
