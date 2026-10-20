"""Mocked hidraw transport checks. No real device is opened or queried."""

from pathlib import Path
import errno
import os
import stat
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import transport
import profile as profile_model
import protocol


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sysfs_root = self.root / "class"
        self.dev_root = self.root / "dev"
        self.usb = self.root / "devices" / "usb1" / "1-2"
        self.interface = self.usb / "1-2:1.2"
        self.hid = self.interface / "0003:320F:5088.0001"
        self.hid.mkdir(parents=True)
        self.sysfs_root.mkdir()
        self.dev_root.mkdir()
        for name, value in [("idVendor", "320f"), ("idProduct", "5088"), ("bcdDevice", "0088")]:
            (self.usb / name).write_text(value)
        (self.interface / "bInterfaceNumber").write_text("02")
        (self.hid / "report_descriptor").write_bytes(transport.EXPECTED_DESCRIPTOR)
        self.class_path = self.sysfs_root / "hidraw73"
        self.class_path.mkdir()
        (self.class_path / "device").symlink_to(self.hid)
        (self.class_path / "dev").write_text("237:73")
        self.node = self.dev_root / "hidraw73"
        self.node.write_bytes(b"")  # Regular fixture; lstat/fstat are mocked below.
        self.node_stat = SimpleNamespace(st_mode=stat.S_IFCHR | 0o660, st_rdev=os.makedev(237, 73), st_dev=1, st_ino=1200)
        self.fd_stat = self.node_stat
        self.raw_info = (3, 0x320F, 0x5088)
        self.kernel_descriptor = transport.EXPECTED_DESCRIPTOR
        self.kernel_descriptor_size = len(self.kernel_descriptor)
        self.returned_descriptor_size = len(self.kernel_descriptor)
        self.send_result = transport.REPORT_LENGTH
        self.receive_result = transport.REPORT_LENGTH
        self.response = bytes([7, 0x8A] + [0] * 262)
        self.sent = []
        self.requests = []
        self.ioctl_failure = None
        self.ioctl_hook = None
        self.device = {
            "vendor": "320f", "product": "5088", "revision": "0088",
            "supported_identity": True, "sysfs_usb": str(self.usb),
            "interfaces": [{
                "number": "02", "node": str(self.node),
                "descriptor_sha256": transport.EXPECTED_DESCRIPTOR_SHA256,
                "report_descriptor_hex": transport.EXPECTED_DESCRIPTOR.hex(),
            }],
        }
        original_lstat = Path.lstat
        def fake_lstat(path, *args, **kwargs):
            if path == self.node:
                return self.node_stat
            return original_lstat(path, *args, **kwargs)
        patches = [
            mock.patch.object(transport, "SYSFS_ROOT", self.sysfs_root),
            mock.patch.object(transport, "DEV_ROOT", self.dev_root),
            mock.patch.object(Path, "lstat", fake_lstat),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.open_mock = self.start_patch("os.open", return_value=73)
        self.close_mock = self.start_patch("os.close")
        self.fstat_mock = self.start_patch("os.fstat", side_effect=lambda fd: self.fd_stat)
        self.flock_mock = self.start_patch("fcntl.flock")
        self.ioctl_mock = self.start_patch("fcntl.ioctl", side_effect=self.fake_ioctl)

    def start_patch(self, name, **kwargs):
        patcher = mock.patch(name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def fake_ioctl(self, fd, request, buffer, mutate):
        self.assertEqual(fd, 73)
        self.assertIs(mutate, True)
        self.assertIsInstance(buffer, bytearray)
        self.requests.append(request)
        if self.ioctl_hook is not None:
            self.ioctl_hook(request)
        if request == self.ioctl_failure:
            raise OSError("simulated ioctl failure")
        if request == transport.HIDIOCGRAWINFO:
            struct.pack_into("=IHH", buffer, 0, *self.raw_info)
            return 0
        if request == transport.HIDIOCGRDESCSIZE:
            struct.pack_into("=I", buffer, 0, self.kernel_descriptor_size)
            return 0
        if request == transport.HIDIOCGRDESC:
            self.assertEqual(len(buffer), 4100)
            self.assertEqual(struct.unpack_from("=I", buffer)[0], len(transport.EXPECTED_DESCRIPTOR))
            struct.pack_into("=I", buffer, 0, self.returned_descriptor_size)
            buffer[4:4 + len(self.kernel_descriptor)] = self.kernel_descriptor
            return 0
        if request == transport.HIDIOCSFEATURE:
            self.sent.append(bytes(buffer))
            return self.send_result
        if request == transport.HIDIOCGFEATURE:
            self.assertEqual(len(buffer), 264)
            self.assertEqual(buffer[0], 7)
            self.assertEqual(bytes(buffer[1:]), bytes(263))
            buffer[:] = self.response
            return self.receive_result
        raise AssertionError(f"Unexpected ioctl: {request:#x}")

    def packet(self, command=0x8A):
        item = profile_model.new_document()["profiles"][0]
        if command == 0x8A:
            return protocol.build_status_request()
        if command == 0x03:
            return protocol.build_keymap(item)
        if command == 0x01:
            return protocol.build_select_profile()
        if command == 0x02:
            return protocol.build_rgb_effect(item["rgb"])
        if command == 0x07:
            return protocol.build_rgb_settings(item["rgb"], num_lock_indicator=True, battery_indicator=True, sensitivity=50, wireless_brightness=70)
        if command == 0x06:
            return protocol.build_per_key_rgb(item["rgb"])
        return bytes([7, command] + [0] * 262)

    def assert_no_features(self):
        self.assertNotIn(transport.HIDIOCSFEATURE, self.requests)
        self.assertNotIn(transport.HIDIOCGFEATURE, self.requests)

    def test_linux_ioctl_values_match_installed_header_verification(self):
        self.assertEqual(transport.HIDIOCGRDESCSIZE, 0x80044801)
        self.assertEqual(transport.HIDIOCGRDESC, 0x90044802)
        self.assertEqual(transport.HIDIOCGRAWINFO, 0x80084803)
        self.assertEqual(transport.HIDIOCSFEATURE, 0xC1084806)
        self.assertEqual(transport.HIDIOCGFEATURE, 0xC1084807)

    def test_constructing_does_not_open_or_query_hid_device(self):
        connection = transport.StockTransport(self.device)
        self.assertFalse(connection.is_open)
        self.open_mock.assert_not_called()
        self.ioctl_mock.assert_not_called()

    def test_valid_context_checks_identity_and_closes_without_vendor_commands(self):
        with transport.StockTransport(self.device) as connection:
            self.assertTrue(connection.is_open)
            flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
            self.open_mock.assert_called_once_with(self.node, flags)
            self.flock_mock.assert_called_once_with(73, transport.fcntl.LOCK_EX | transport.fcntl.LOCK_NB)
            self.assert_no_features()
        self.assertFalse(connection.is_open)
        self.close_mock.assert_called_once_with(73)

    def test_all_explicitly_allowed_commands_send_with_revalidation(self):
        with transport.StockTransport(self.device) as connection:
            for command in (0x8A, 0x03, 0x01, 0x02, 0x07, 0x06):
                connection.send_feature(self.packet(command))
            self.assertEqual(self.sent, [self.packet(command) for command in (0x8A, 0x03, 0x01, 0x02, 0x07, 0x06)])
            self.assertEqual(self.requests.count(transport.HIDIOCGRAWINFO), 7)
            self.assertEqual(self.requests.count(transport.HIDIOCGRDESC), 7)

    def test_receive_returns_full_fixed_size_report_including_report_id(self):
        with transport.StockTransport(self.device) as connection:
            self.assertEqual(connection.get_feature(), self.response)
            self.assertEqual(self.requests.count(transport.HIDIOCGRAWINFO), 2)

    def test_nonallowlisted_commands_close_connection_before_feature_ioctl(self):
        for command in (0x00, 0x04, 0x05, 0xFF):
            with self.subTest(command=command):
                self.requests.clear()
                with transport.StockTransport(self.device) as connection:
                    with self.assertRaises(transport.UnsupportedCommand):
                        connection.send_feature(self.packet(command))
                    self.assertFalse(connection.is_open)
                self.assert_no_features()

    def test_command_bounds_fixed_fields_and_reserved_bytes_are_checked(self):
        cases = [
            (0x8A, 2, 1),       # Status has no payload.
            (0x01, 2, 0),       # Slots and layers are one based.
            (0x01, 3, 4),
            (0x01, 4, 1),       # Selection has no extra payload.
            (0x03, 4, 1),       # Keymap reserved header.
            (0x03, 8 + 11, 1),  # Encoder press must remain calculator.
            (0x03, 28 + 11, 4),
            (0x03, 8 + 17, 1),  # Matrix holes must remain empty.
            (0x03, 28 + 18, 1),
            (0x03, 8, 0x03),    # No alternate key-action families.
            (0x03, 48, 1),      # Keymap tail reserved.
            (0x02, 4, 255),     # Only reviewed RGB effects.
            (0x02, 8, 0),       # Stock speed representation 1..20.
            (0x02, 8, 21),
            (0x02, 12, 1),      # Rainbow flag must match effect.
            (0x02, 18, 1),
            (0x07, 5, 1),       # Win Lock fixed at zero.
            (0x07, 6, 0),       # Per-key enable fixed at one.
            (0x07, 7, 2),       # Indicator flags are boolean.
            (0x07, 8, 21),
            (0x07, 10, 9),      # Latency fixed at eight.
            (0x07, 11, 51),
            (0x07, 12, 2),
            (0x07, 13, 21),
            (0x07, 14, 1),
            (0x06, 4, 41),      # Complete 42-record matrix only.
            (0x06, 6, 21),
            (0x06, 8, 0xFF),    # Sidelights cannot carry key overrides.
            (0x06, 9, 1),       # LED index must match its position.
            (0x06, 8 + 1 * 5 + 2, 1),  # Unassigned record RGB must be zero.
            (0x06, 8 + 1 * 5, 0xFE),   # Breathing override is not implemented.
            (0x06, 8 + 13 * 5, 0xFF),  # Unused matrix record must be zero.
            (0x06, 218, 1),
        ]
        for command, offset, value in cases:
            with self.subTest(command=command, offset=offset, value=value):
                self.requests.clear()
                report = bytearray(self.packet(command))
                report[offset] = value
                with transport.StockTransport(self.device) as connection:
                    with self.assertRaises(transport.InvalidReport):
                        connection.send_feature(report)
                    self.assertFalse(connection.is_open)
                self.assert_no_features()

    def test_all_reviewed_builder_effects_modifiers_and_boundary_settings_pass(self):
        item = profile_model.new_document()["profiles"][0]
        with transport.StockTransport(self.device) as connection:
            for effect in profile_model.RGB_EFFECTS:
                for value in (0, 100):
                    rgb = dict(item["rgb"], effect=effect, speed=value, brightness=value)
                    connection.send_feature(protocol.build_rgb_effect(rgb, 3, 3))
                    connection.send_feature(protocol.build_rgb_settings(rgb, 3, 3, num_lock_indicator=False, battery_indicator=False, sensitivity=value, wireless_brightness=value))
                    connection.send_feature(protocol.build_per_key_rgb(rgb, 3, 3))
            item["bindings"]["enter"]["modifiers"] = ["ctrl", "shift", "alt", "super"]
            connection.send_feature(protocol.build_keymap(item, 3, 3))

    def test_rgb_layout_accepts_only_explicit_physical_overrides_and_zero_unassigned_records(self):
        item = profile_model.new_document()["profiles"][0]
        item["rgb"]["per_key"] = {"enter": "#123456", "num_lock": "#ffffff"}
        with transport.StockTransport(self.device) as connection:
            for effect in profile_model.RGB_EFFECTS:
                item["rgb"]["effect"] = effect
                connection.send_feature(protocol.build_per_key_rgb(item["rgb"]))

    def test_busy_advisory_lock_closes_fd_without_metadata_or_feature_ioctls(self):
        self.flock_mock.side_effect = BlockingIOError(errno.EAGAIN, "another editor is using this device")
        connection = transport.StockTransport(self.device)
        with self.assertRaisesRegex(transport.TransportError, "another editor"):
            connection.__enter__()
        self.assertFalse(connection.is_open)
        self.close_mock.assert_called_once_with(73)
        self.ioctl_mock.assert_not_called()

    def test_invalid_size_id_and_type_close_connection_without_feature_ioctl(self):
        packets = [b"", bytes(263), bytes(265), bytes([6, 0x8A] + [0] * 262), "not bytes", None]
        for packet in packets:
            with self.subTest(packet=repr(packet)[:40]):
                self.requests.clear()
                with transport.StockTransport(self.device) as connection:
                    with self.assertRaises(transport.InvalidReport):
                        connection.send_feature(packet)
                    self.assertFalse(connection.is_open)
                self.assert_no_features()

    def test_metadata_spoofing_and_invalid_interface_are_rejected_before_open(self):
        cases = [
            dict(self.device, vendor="1234"), dict(self.device, product="9999"),
            dict(self.device, revision="0099"), dict(self.device, supported_identity=False),
            dict(self.device, interfaces=[]), dict(self.device, interfaces=self.device["interfaces"] * 2),
            dict(self.device, interfaces=[dict(self.device["interfaces"][0], number="01")]),
            dict(self.device, interfaces=[dict(self.device["interfaces"][0], descriptor_sha256="0" * 64)]),
            dict(self.device, interfaces=[dict(self.device["interfaces"][0], node="/tmp/arbitrary")]),
        ]
        for candidate in cases:
            with self.subTest(candidate=candidate):
                with self.assertRaises(transport.DeviceValidationError):
                    transport.StockTransport(candidate)
        self.open_mock.assert_not_called()

    def test_live_sysfs_revision_change_is_rejected_before_open(self):
        connection = transport.StockTransport(self.device)
        (self.usb / "bcdDevice").write_text("0099")
        with self.assertRaises(transport.DeviceValidationError):
            connection.__enter__()
        self.open_mock.assert_not_called()
        self.assert_no_features()

    def test_symlink_or_regular_node_is_rejected_before_open(self):
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFREG | 0o660):
            with self.subTest(mode=mode):
                self.node_stat = SimpleNamespace(**dict(vars(self.node_stat), st_mode=mode))
                with self.assertRaises(transport.DeviceValidationError):
                    transport.StockTransport(self.device).__enter__()
        self.open_mock.assert_not_called()

    def test_sysfs_device_number_and_open_fd_device_number_must_match(self):
        (self.class_path / "dev").write_text("237:74")
        with self.assertRaises(transport.DeviceValidationError):
            transport.StockTransport(self.device).__enter__()
        self.open_mock.assert_not_called()
        (self.class_path / "dev").write_text("237:73")
        self.fd_stat = SimpleNamespace(**dict(vars(self.node_stat), st_rdev=os.makedev(237, 74)))
        with self.assertRaises(transport.DeviceValidationError):
            transport.StockTransport(self.device).__enter__()
        self.close_mock.assert_called_once_with(73)
        self.assert_no_features()

    def test_open_fd_inode_must_match_path_inode(self):
        self.fd_stat = SimpleNamespace(**dict(vars(self.node_stat), st_ino=1300))
        with self.assertRaises(transport.DeviceValidationError):
            transport.StockTransport(self.device).__enter__()
        self.close_mock.assert_called_once_with(73)
        self.ioctl_mock.assert_not_called()

    def test_wrong_kernel_bus_vendor_or_product_closes_open_fd(self):
        for raw_info in [(5, 0x320F, 0x5088), (3, 0x1234, 0x5088), (3, 0x320F, 0x9999)]:
            with self.subTest(raw_info=raw_info):
                self.raw_info = raw_info
                self.requests.clear()
                with self.assertRaises(transport.DeviceValidationError):
                    transport.StockTransport(self.device).__enter__()
                self.assert_no_features()
        self.assertEqual(self.close_mock.call_count, 3)

    def test_wrong_kernel_descriptor_size_or_bytes_closes_open_fd(self):
        for size, descriptor, returned in [
            (25, transport.EXPECTED_DESCRIPTOR, 24),
            (24, bytes(24), 24),
            (24, transport.EXPECTED_DESCRIPTOR, 23),
        ]:
            with self.subTest(size=size, descriptor=descriptor, returned=returned):
                self.kernel_descriptor_size, self.kernel_descriptor, self.returned_descriptor_size = size, descriptor, returned
                with self.assertRaises(transport.DeviceValidationError):
                    transport.StockTransport(self.device).__enter__()
        self.assert_no_features()
        self.assertEqual(self.close_mock.call_count, 3)

    def test_current_sysfs_descriptor_is_checked_instead_of_trusting_metadata(self):
        (self.hid / "report_descriptor").write_bytes(bytes(24))
        with self.assertRaises(transport.DeviceValidationError):
            transport.StockTransport(self.device).__enter__()
        self.open_mock.assert_not_called()

    def test_path_replacement_between_validation_and_send_is_rejected(self):
        with transport.StockTransport(self.device) as connection:
            self.node_stat = SimpleNamespace(**dict(vars(self.node_stat), st_ino=1300))
            with self.assertRaises(transport.DeviceValidationError):
                connection.send_feature(self.packet())
            self.assertFalse(connection.is_open)
        self.assert_no_features()

    def test_usb_lineage_replacement_is_rejected_before_send(self):
        with transport.StockTransport(self.device) as connection:
            replacement = self.interface / "0003:320F:5088.9999"
            replacement.mkdir()
            (replacement / "report_descriptor").write_bytes(transport.EXPECTED_DESCRIPTOR)
            (self.class_path / "device").unlink()
            (self.class_path / "device").symlink_to(replacement)
            with self.assertRaises(transport.DeviceValidationError):
                connection.send_feature(self.packet())
        self.assert_no_features()

    def test_revalidation_catches_change_during_metadata_ioctls(self):
        with transport.StockTransport(self.device) as connection:
            def replace_after_metadata(request):
                if request == transport.HIDIOCGRDESC:
                    self.node_stat = SimpleNamespace(**dict(vars(self.node_stat), st_ino=1300))
            self.ioctl_hook = replace_after_metadata
            with self.assertRaises(transport.DeviceValidationError):
                connection.send_feature(self.packet())
        self.assert_no_features()

    def test_short_feature_send_and_receive_and_wrong_response_id_close_fd(self):
        self.send_result = 263
        with transport.StockTransport(self.device) as connection:
            with self.assertRaises(transport.TransportError):
                connection.send_feature(self.packet())
            self.assertFalse(connection.is_open)
        self.receive_result = 263
        with transport.StockTransport(self.device) as connection:
            with self.assertRaises(transport.TransportError):
                connection.get_feature()
            self.assertFalse(connection.is_open)
        self.receive_result = 264
        self.response = bytes([6] + [0] * 263)
        with transport.StockTransport(self.device) as connection:
            with self.assertRaises(transport.InvalidReport):
                connection.get_feature()
            self.assertFalse(connection.is_open)

    def test_open_error_and_ioctl_error_leave_transport_closed(self):
        self.open_mock.side_effect = PermissionError("simulated access denial")
        connection = transport.StockTransport(self.device)
        with self.assertRaises(transport.TransportError):
            connection.__enter__()
        self.assertFalse(connection.is_open)
        self.close_mock.assert_not_called()
        self.open_mock.side_effect = None
        self.ioctl_failure = transport.HIDIOCGRAWINFO
        connection = transport.StockTransport(self.device)
        with self.assertRaises(transport.TransportError):
            connection.__enter__()
        self.assertFalse(connection.is_open)
        self.close_mock.assert_called_once_with(73)

    def test_closed_transport_refuses_operations_and_close_is_idempotent(self):
        connection = transport.StockTransport(self.device)
        with self.assertRaises(transport.TransportError):
            connection.get_feature()
        with connection:
            pass
        connection.close()
        self.close_mock.assert_called_once_with(73)
        with self.assertRaises(transport.TransportError):
            connection.send_feature(self.packet())

    def test_context_body_exception_still_closes_device(self):
        connection = transport.StockTransport(self.device)
        with self.assertRaisesRegex(RuntimeError, "caller failure"):
            with connection:
                raise RuntimeError("caller failure")
        self.assertFalse(connection.is_open)
        self.close_mock.assert_called_once_with(73)


if __name__ == "__main__":
    unittest.main()
