"""Connection identity, battery provenance, freshness, and polling without HID."""
import threading
import unittest
from pathlib import Path
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from monitoring import (BluetoothObservation, MonitorCoordinator, MonitorSnapshot,
                        SystemBluetoothProvider, BLUEZ_DEVICE, BLUEZ_BATTERY,
                        bluetooth_from_objects, upower_battery_for_device)


PATH = "/org/bluez/hci0/dev_01_02_03_04_05_06"
OTHER_PATH = "/org/bluez/hci1/dev_11_12_13_14_15_16"


def bt_object(*, modalias="usb:v093Ap2860d0001", connected=True, paired=True,
              name="GMMK Numpad", percent=62):
    properties = {"Paired": paired, "Connected": connected, "Name": name}
    if modalias is not None:
        properties["Modalias"] = modalias
    result = {BLUEZ_DEVICE: properties}
    if percent is not None:
        result[BLUEZ_BATTERY] = {"Percentage": percent}
    return result


def usb_device(path="/sys/devices/usb1/1-2", *, revision="0088"):
    return {"vendor": "320f", "product": "5088", "revision": revision,
            "supported_identity": revision == "0088", "sysfs_usb": path}


class BluetoothResolverTests(unittest.TestCase):
    def test_documented_identity_uses_battery_on_same_object(self):
        observation = bluetooth_from_objects({PATH: bt_object()})
        self.assertTrue(observation.connected)
        self.assertTrue(observation.identity_verified)
        self.assertEqual(observation.battery_percent, 62)
        self.assertEqual(observation.source, "bluez")
        self.assertIsNone(observation.charging)

    def test_usb_identity_is_also_recognized(self):
        self.assertTrue(bluetooth_from_objects({PATH: bt_object(modalias="usb:v320Fp5088d0088")}).identity_verified)

    def test_disconnected_device_never_supplies_cached_battery(self):
        observation = bluetooth_from_objects({PATH: bt_object(connected=False)})
        self.assertFalse(observation.connected)
        self.assertIsNone(observation.battery_percent)

    def test_unpaired_advertisement_is_not_connected_target(self):
        self.assertFalse(bluetooth_from_objects({PATH: bt_object(paired=False)}).connected)

    def test_conflicting_modalias_cannot_be_overridden_by_name(self):
        observation = bluetooth_from_objects({PATH: bt_object(modalias="usb:v1234p5678d0001")})
        self.assertFalse(observation.connected)

    def test_exact_name_fallback_is_display_only(self):
        observation = bluetooth_from_objects({PATH: bt_object(modalias=None)})
        self.assertTrue(observation.connected)
        self.assertFalse(observation.identity_verified)
        self.assertIsNone(observation.battery_percent)
        self.assertIsNone(observation.source)

    def test_alias_and_partial_names_cannot_identify_numpad(self):
        item = bt_object(modalias=None, name="Other keyboard")
        item[BLUEZ_DEVICE]["Alias"] = "GMMK Numpad"
        self.assertFalse(bluetooth_from_objects({PATH: item}).connected)
        self.assertFalse(bluetooth_from_objects({PATH: bt_object(modalias=None, name="GMMK Numpad Clone")}).connected)

    def test_two_connected_matches_fail_closed(self):
        observation = bluetooth_from_objects({PATH: bt_object(), OTHER_PATH: bt_object(percent=99)})
        self.assertTrue(observation.ambiguous)
        self.assertIsNone(observation.battery_percent)
        self.assertIsNone(observation.path)

    def test_other_device_battery_cannot_fill_missing_target_battery(self):
        observation = bluetooth_from_objects({PATH: bt_object(percent=None), OTHER_PATH: bt_object(modalias="usb:v1234p5678d0001", percent=99)})
        self.assertTrue(observation.connected)
        self.assertIsNone(observation.battery_percent)

    def test_invalid_battery_values_are_not_clamped_or_fabricated(self):
        for value in (True, -1, 101, 12.4, "62", None):
            with self.subTest(value=value):
                self.assertIsNone(bluetooth_from_objects({PATH: bt_object(percent=value)}).battery_percent)
        self.assertEqual(bluetooth_from_objects({PATH: bt_object(percent=0)}).battery_percent, 0)

    def test_malformed_manager_data_fails_closed(self):
        for value in (None, [], "bad"):
            self.assertFalse(bluetooth_from_objects(value).connected)
        self.assertFalse(bluetooth_from_objects({"/arbitrary": bt_object()}).connected)


class UPowerAssociationTests(unittest.TestCase):
    def battery(self, **changes):
        result = {"NativePath": PATH, "IsPresent": True, "PowerSupply": False,
                  "Percentage": 61.6, "BatteryLevel": 1, "State": 1}
        result.update(changes)
        return result

    def test_exact_native_path_accepts_percentage_and_documented_charging(self):
        self.assertEqual(upower_battery_for_device([self.battery()], PATH), (62, True))

    def test_exact_complete_address_is_allowed(self):
        self.assertEqual(upower_battery_for_device([self.battery(NativePath="01:02:03:04:05:06", State=2)], PATH), (62, False))

    def test_similar_name_or_address_substring_cannot_associate_battery(self):
        for native in ("GMMK Numpad", "/other" + PATH, "01:02:03:04:05:06-clone", OTHER_PATH):
            self.assertEqual(upower_battery_for_device([self.battery(NativePath=native, Model="GMMK Numpad")], PATH), (None, None))

    def test_duplicate_associations_are_ambiguous(self):
        self.assertEqual(upower_battery_for_device([self.battery(), self.battery()], PATH), (None, None))

    def test_absent_laptop_and_coarse_values_are_rejected(self):
        for change in ({"IsPresent": False}, {"PowerSupply": True}, {"BatteryLevel": 6}, {"Percentage": float("nan")}, {"Percentage": 101}):
            self.assertEqual(upower_battery_for_device([self.battery(**change)], PATH), (None, None))

    def test_unknown_and_pending_state_are_not_reported_as_charging(self):
        for state in (0, 5, 6, None, True):
            self.assertEqual(upower_battery_for_device([self.battery(State=state)], PATH), (62, None))


class SystemProviderTests(unittest.TestCase):
    def test_bluez_read_does_not_request_upower_when_battery_exists(self):
        provider = SystemBluetoothProvider()
        provider._call = Mock(return_value={PATH: bt_object()})
        self.assertEqual(provider.read().battery_percent, 62)
        self.assertEqual(provider._call.call_count, 1)
        self.assertEqual(provider._call.call_args.args[3], "GetManagedObjects")

    def test_name_fallback_never_queries_battery_or_upower(self):
        provider = SystemBluetoothProvider()
        provider._call = Mock(return_value={PATH: bt_object(modalias=None)})
        self.assertIsNone(provider.read().battery_percent)
        self.assertEqual(provider._call.call_count, 1)

    def test_verified_missing_battery_can_use_exact_upower_native_path(self):
        provider = SystemBluetoothProvider()
        provider._call = Mock(side_effect=[
            {PATH: bt_object(percent=None)}, ["/org/freedesktop/UPower/devices/keyboard_test"],
            {"NativePath": PATH, "IsPresent": True, "PowerSupply": False, "Percentage": 79.0, "State": 4}])
        observation = provider.read()
        self.assertEqual(observation.battery_percent, 79)
        self.assertEqual(observation.source, "upower")
        self.assertFalse(observation.charging)
        self.assertEqual([call.args[3] for call in provider._call.call_args_list],
                         ["GetManagedObjects", "EnumerateDevices", "GetAll"])

    def test_dbus_errors_are_friendly_and_do_not_expose_addresses(self):
        provider = SystemBluetoothProvider()
        provider._call = Mock(side_effect=RuntimeError(PATH))
        observation = provider.read()
        self.assertEqual(observation.error, "Bluetooth status is unavailable.")
        self.assertNotIn(PATH, observation.error)

    def test_gi_remains_optional_until_system_provider_is_used(self):
        with patch.dict(sys.modules, {"gi": None, "gi.repository": None}):
            provider = SystemBluetoothProvider()
            self.assertEqual(provider.read().error, "Bluetooth status is unavailable.")


class FakeClock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.scan = Mock(return_value=[usb_device()])
        self.status = Mock(return_value={"battery": 83, "charging": 1, "firmware_version": "0012"})
        self.bluetooth = Mock()
        self.bluetooth.read.return_value = BluetoothObservation()
        self.monitor = MonitorCoordinator(scan_usb=self.scan, read_usb=self.status,
            bluetooth=self.bluetooth, monotonic=self.clock, wall_clock=lambda: 1000 + self.clock.now)

    def test_usb_reading_and_unknown_charging(self):
        result = self.monitor.poll()
        self.assertEqual(result.transport, "usb")
        self.assertEqual(result.battery_percent, 83)
        self.assertEqual(result.timestamp, 1000)
        self.assertEqual(result.firmware, "0012")
        self.assertIsNone(result.charging)
        self.assertFalse(result.stale)

    def test_monitor_never_sends_configuration(self):
        self.monitor.poll()
        self.status.assert_called_once_with()

    def test_default_usb_reader_binds_the_query_to_the_scanned_identity(self):
        monitor = MonitorCoordinator(scan_usb=self.scan, bluetooth=self.bluetooth,
                                     monotonic=self.clock, wall_clock=lambda: 1000)
        with patch("backend.read_status", return_value={"battery": 81}) as read:
            result = monitor.poll()
        read.assert_called_once_with(device=self.scan.return_value[0])
        self.assertEqual(result.battery_percent, 81)

    def test_connection_and_battery_polling_are_rate_limited_separately(self):
        first = self.monitor.poll()
        self.clock.now = 4
        self.assertIs(self.monitor.poll(), first)
        self.scan.assert_called_once()
        self.clock.now = 5
        self.monitor.poll()
        self.assertEqual(self.scan.call_count, 2)
        self.status.assert_called_once()
        self.clock.now = 30
        self.monitor.poll()
        self.assertEqual(self.status.call_count, 2)

    def test_force_refresh_does_not_wait_for_intervals(self):
        self.monitor.poll()
        self.monitor.poll(force=True)
        self.assertEqual(self.status.call_count, 2)

    def test_busy_read_retains_last_good_battery_until_ttl(self):
        self.monitor.poll()
        self.status.side_effect = RuntimeError("busy")
        self.clock.now = 30
        result = self.monitor.poll()
        self.assertEqual(result.battery_percent, 83)
        self.assertEqual(result.timestamp, 1000)
        self.assertTrue(result.stale)
        self.clock.now = 120
        result = self.monitor.poll()
        self.assertIsNone(result.battery_percent)
        self.assertIsNone(result.source)
        self.assertTrue(result.stale)

    def test_invalid_status_does_not_become_reading(self):
        self.status.return_value = {"battery": 101}
        result = self.monitor.poll()
        self.assertIsNone(result.battery_percent)
        self.assertIsNotNone(result.error)

    def test_disconnect_clears_cached_battery_immediately(self):
        self.monitor.poll()
        self.scan.return_value = []
        self.clock.now = 5
        result = self.monitor.poll()
        self.assertEqual(result.transport, "disconnected")
        self.assertIsNone(result.battery_percent)

    def test_usb_identity_change_does_not_keep_previous_device_battery(self):
        self.monitor.poll()
        self.scan.return_value = [usb_device("/sys/devices/usb1/1-3")]
        self.status.side_effect = RuntimeError("busy")
        self.clock.now = 5
        self.assertIsNone(self.monitor.poll().battery_percent)
        self.assertEqual(self.status.call_count, 2)

    def test_usb_ambiguity_sends_no_status_query(self):
        self.scan.return_value = [usb_device(), usb_device("/sys/devices/usb1/1-3")]
        result = self.monitor.poll()
        self.assertEqual(result.transport, "ambiguous")
        self.assertTrue(result.usb_connected)
        self.status.assert_not_called()

    def test_unsupported_usb_revision_sends_no_status_query(self):
        self.scan.return_value = [usb_device(revision="0099")]
        self.assertFalse(self.monitor.poll().usb_connected)
        self.status.assert_not_called()

    def test_bluetooth_reading_is_never_taken_from_disconnected_target(self):
        self.scan.return_value = []
        self.bluetooth.read.return_value = BluetoothObservation(connected=False, battery_percent=77, path=PATH)
        self.assertIsNone(self.monitor.poll().battery_percent)
        self.status.assert_not_called()

    def test_usb_display_preferred_when_both_connections_exist(self):
        self.bluetooth.read.return_value = BluetoothObservation(connected=True, identity_verified=True,
            path=PATH, battery_percent=5, source="bluez")
        result = self.monitor.poll()
        self.assertEqual(result.transport, "usb")
        self.assertTrue(result.bluetooth_connected)
        self.assertEqual(result.battery_percent, 83)

    def test_usb_error_cannot_substitute_another_bluetooth_device_battery(self):
        self.status.side_effect = RuntimeError("denied")
        self.bluetooth.read.return_value = BluetoothObservation(connected=True, identity_verified=True,
            path=PATH, battery_percent=77, source="bluez")
        self.assertIsNone(self.monitor.poll().battery_percent)

    def test_bluetooth_has_no_vendor_status_query(self):
        self.scan.return_value = []
        self.bluetooth.read.return_value = BluetoothObservation(connected=True, identity_verified=True,
            path=PATH, battery_percent=77, source="bluez")
        result = self.monitor.poll()
        self.assertEqual(result.transport, "bluetooth")
        self.assertEqual(result.battery_percent, 77)
        self.status.assert_not_called()

    def test_name_only_bluetooth_can_only_display_connection(self):
        self.scan.return_value = []
        self.bluetooth.read.return_value = BluetoothObservation(connected=True, path=PATH, battery_percent=77)
        result = self.monitor.poll()
        self.assertEqual(result.transport, "bluetooth")
        self.assertIsNone(result.battery_percent)

    def test_overlap_returns_immediately_without_duplicate_reads(self):
        started, release = threading.Event(), threading.Event()
        def blocked_status():
            started.set()
            release.wait(2)
            return {"battery": 80}
        self.status.side_effect = blocked_status
        thread = threading.Thread(target=self.monitor.poll)
        thread.start()
        self.assertTrue(started.wait(1))
        try:
            self.assertEqual(self.monitor.poll(force=True), MonitorSnapshot())
            self.status.assert_called_once()
        finally:
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_recovery_clears_error_and_stale(self):
        self.monitor.poll()
        self.status.side_effect = RuntimeError("busy")
        self.clock.now = 30
        self.assertTrue(self.monitor.poll().stale)
        self.status.side_effect = None
        self.status.return_value = {"battery": 84}
        self.clock.now = 60
        result = self.monitor.poll()
        self.assertFalse(result.stale)
        self.assertIsNone(result.error)
        self.assertEqual(result.battery_percent, 84)


if __name__ == "__main__":
    unittest.main()
