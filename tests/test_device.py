"""Synthetic sysfs tests; never access or send commands to a real HID node."""

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import device


FEATURE_DESCRIPTOR = bytes.fromhex("0601ff0901a1018507150026ff0009207508960701b102c0")
SLIDER_DESCRIPTOR = bytes.fromhex("0600ff0a00ffa1018504150026ff00093075089508810209319102c0")


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sysfs = self.root / "class"
        self.dev = self.root / "dev"
        self.sysfs.mkdir()
        self.dev.mkdir()

    def add_device(self, usb_name="1-2", vendor="320f", product="5088", revision="0088", numbers=("00", "01", "02"), start_node=37):
        usb = self.root / "usb1" / usb_name
        usb.mkdir(parents=True)
        for name, value in [("idVendor", vendor), ("idProduct", product), ("bcdDevice", revision), ("manufacturer", "Glorious"), ("product", "GMMK Numpad")]:
            (usb / name).write_text(value + "\n")
        for offset, number in enumerate(numbers):
            interface = usb / f"{usb_name}:1.{int(number, 16)}"
            hid = interface / f"0003:320F:5088.{offset:04d}"
            hid.mkdir(parents=True)
            (interface / "bInterfaceNumber").write_text(number)
            (hid / "report_descriptor").write_bytes(FEATURE_DESCRIPTOR if number == "02" else SLIDER_DESCRIPTOR)
            entry = self.sysfs / f"hidraw{start_node + offset}"
            entry.mkdir()
            (entry / "device").symlink_to(hid)
        return usb

    def scan(self):
        return device.scan_devices(str(self.sysfs), str(self.dev))

    def test_groups_all_interfaces_and_does_not_assume_node_numbers(self):
        usb = self.add_device()
        found = self.scan()
        self.assertEqual(len(found), 1)
        selected = device.select_device(found)
        self.assertEqual(selected["sysfs_usb"], str(usb))
        self.assertEqual([i["number"] for i in selected["interfaces"]], ["00", "01", "02"])
        self.assertEqual(Path(selected["interfaces"][2]["node"]).name, "hidraw39")
        feature = selected["interfaces"][2]
        self.assertEqual(feature["report_descriptor_hex"], FEATURE_DESCRIPTOR.hex())
        self.assertEqual(feature["descriptor_sha256"], hashlib.sha256(FEATURE_DESCRIPTOR).hexdigest())
        self.assertEqual(feature["reports"], [{"report_type": "feature", "report_id": 7, "data_bits": 2104, "data_bytes": 263, "total_bytes": 264}])
        self.assertFalse(feature["node_available"])
        self.assertFalse(feature["readable"])
        self.assertFalse(feature["writable"])

    def test_wrong_vendor_or_product_is_rejected(self):
        self.add_device(usb_name="1-1", vendor="1234")
        self.add_device(usb_name="1-2", product="9999", start_node=47)
        self.assertEqual(self.scan(), [])

    def test_different_revision_is_visible_but_not_selectable(self):
        self.add_device(revision="0099")
        found = self.scan()
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]["supported_identity"])
        self.assertIsNone(device.select_device(found))

    def test_multiple_physical_devices_refuse_automatic_selection(self):
        self.add_device(usb_name="1-1")
        self.add_device(usb_name="1-2", start_node=47)
        found = self.scan()
        self.assertEqual(len(found), 2)
        self.assertIsNone(device.select_device(found))
        self.assertIsNone(device.select_device([]))

    def test_selection_does_not_trust_supported_identity_flag_alone(self):
        spoofed = {"vendor": "wrong", "product": "5088", "revision": "0088", "supported_identity": True}
        self.assertIsNone(device.select_device([spoofed]))

    def test_missing_and_broken_entries_do_not_break_scan(self):
        self.add_device()
        broken = self.sysfs / "hidraw999"
        broken.mkdir()
        (broken / "device").symlink_to(self.root / "missing")
        self.assertEqual(len(self.scan()), 1)
        self.assertEqual(device.scan_devices(str(self.root / "absent"), str(self.dev)), [])

    def test_nearest_usb_parent_prevents_matching_hub_ancestor(self):
        usb = self.add_device(vendor="1234")
        for name, value in [("idVendor", "320f"), ("idProduct", "5088"), ("bcdDevice", "0088")]:
            (usb.parent / name).write_text(value)
        self.assertEqual(self.scan(), [])

    def test_sysfs_scan_never_opens_hidraw_node(self):
        self.add_device()
        (self.dev / "hidraw39").write_bytes(b"must not be read")
        original_open = Path.open
        def guarded_open(path, *args, **kwargs):
            if path.parent == self.dev:
                raise AssertionError("A hidraw node must not be opened")
            return original_open(path, *args, **kwargs)
        with mock.patch.object(Path, "open", guarded_open):
            found = self.scan()
        self.assertTrue(found[0]["interfaces"][2]["node_available"])
        self.assertFalse(found[0]["interfaces"][2]["node_is_character"])

    def test_bad_or_missing_descriptor_still_shows_device(self):
        usb = self.add_device()
        descriptors = list(usb.rglob("report_descriptor"))
        descriptors[0].unlink()
        descriptors[1].write_bytes(b"\x96\x07")
        found = self.scan()
        self.assertEqual(len(found[0]["interfaces"]), 3)
        self.assertEqual(sum("descriptor_error" in i for i in found[0]["interfaces"]), 2)


class DescriptorTests(unittest.TestCase):
    def test_report_id_is_included_and_unnumbered_report_has_no_extra_byte(self):
        self.assertEqual(device.parse_report_descriptor(bytes.fromhex("750895088102"))[0]["total_bytes"], 8)
        reports = device.parse_report_descriptor(SLIDER_DESCRIPTOR)
        self.assertEqual([(r["report_type"], r["total_bytes"]) for r in reports], [("input", 9), ("output", 9)])

    def test_report_sizes_accumulate_and_global_push_pop_restores_state(self):
        reports = device.parse_report_descriptor(bytes.fromhex("8501750195018102a4750895028102b48102"))
        self.assertEqual(reports[0]["data_bits"], 18)
        self.assertEqual(reports[0]["total_bytes"], 4)

    def test_truncated_and_invalid_descriptors_are_rejected(self):
        for descriptor in [b"\x96\x07", b"\xfe", b"\xfe\x02\x00\x01", b"\xb4", b"\x85\x00"]:
            with self.subTest(descriptor=descriptor):
                with self.assertRaises(ValueError):
                    device.parse_report_descriptor(descriptor)


if __name__ == "__main__":
    unittest.main()
