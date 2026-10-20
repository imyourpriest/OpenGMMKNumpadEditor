"""Profile validation, portability, and replacement guarantees."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import profile as profiles


class ProfileTests(unittest.TestCase):
    def legacy_document(self):
        document = profiles.new_document()
        document["schema_version"] = 1
        for item in document["profiles"]:
            del item["rgb"]["device_settings"]
        return document

    def test_defaults_are_independent_local_templates(self):
        document = profiles.new_document()
        profiles.validate_document(document)
        self.assertEqual(document["document_kind"], "local-draft")
        self.assertEqual(document["schema_version"], 2)
        self.assertEqual(document["profiles"][0]["rgb"]["device_settings"], {
            "num_lock_indicator": False, "battery_indicator": True,
            "sensitivity": 2, "wireless_brightness": 70,
        })
        self.assertEqual([p["name"] for p in document["profiles"]], ["Profile 1", "Profile 2", "Profile 3"])
        self.assertEqual(len(profiles.PHYSICAL_KEYS), 17)
        self.assertEqual(len(profiles.KEY_IDS), 17)
        document["profiles"][0]["bindings"]["kp_7"]["modifiers"].append("ctrl")
        document["profiles"][0]["rgb"]["per_key"]["kp_7"] = "#ffffff"
        document["profiles"][0]["rgb"]["device_settings"]["sensitivity"] = 99
        self.assertEqual(document["profiles"][1]["bindings"]["kp_7"]["modifiers"], [])
        self.assertEqual(document["profiles"][1]["rgb"]["per_key"], {})
        self.assertEqual(document["profiles"][1]["rgb"]["device_settings"]["sensitivity"], 2)
        self.assertEqual(profiles.new_document()["profiles"][0]["rgb"]["per_key"], {})

    def test_physical_layout_has_no_overlaps_and_expected_wide_keys(self):
        occupied = set()
        for key in profiles.PHYSICAL_KEYS:
            for row in range(key["row"], key["row"] + key["height"]):
                for col in range(key["col"], key["col"] + key["width"]):
                    self.assertNotIn((row, col), occupied)
                    occupied.add((row, col))
        self.assertEqual(occupied, {(row, col) for row in range(5) for col in range(4)})

    def test_json_round_trip_preserves_multiple_profiles_bindings_and_colors(self):
        document = profiles.new_document()
        document["profiles"][1]["name"] = "Café shortcuts"
        document["profiles"][1]["bindings"]["kp_7"] = {"usage_page": 7, "usage_id": 4, "modifiers": ["ctrl", "shift"]}
        document["profiles"][1]["rgb"].update({"effect": "breathing", "color": "#AABBCC", "brightness": 0, "speed": 100, "per_key": {"enter": "#123456"}})
        document["profiles"][1]["rgb"]["device_settings"].update({
            "num_lock_indicator": True, "battery_indicator": False,
            "sensitivity": 87, "wireless_brightness": 23,
        })
        document["selected_profile_id"] = document["profiles"][1]["id"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "portable.json"
            profiles.save_document(path, document)
            self.assertEqual(profiles.load_document(path), document)
            self.assertIn("Café", path.read_text())

    def test_bad_fields_and_values_are_rejected(self):
        modifications = {
            "unsupported schema": lambda d: d.update(schema_version=3),
            "boolean schema": lambda d: d.update(schema_version=True),
            "hardware claim": lambda d: d.update(document_kind="onboard"),
            "wrong revision": lambda d: d["target"].update(usb_revision="9999"),
            "unknown top field": lambda d: d.update(can_write_device=True),
            "missing profile": lambda d: d.update(selected_profile_id="absent"),
            "no profiles": lambda d: d.update(profiles=[]),
            "duplicate profile id": lambda d: d["profiles"][1].update(id=d["profiles"][0]["id"]),
            "blank name": lambda d: d["profiles"][0].update(name="   "),
            "control in name": lambda d: d["profiles"][0].update(name="bad\nname"),
            "missing binding": lambda d: d["profiles"][0]["bindings"].pop("enter"),
            "invented key": lambda d: d["profiles"][0]["bindings"].update(invented={}),
            "wrong usage page": lambda d: d["profiles"][0]["bindings"]["enter"].update(usage_page=12),
            "negative usage": lambda d: d["profiles"][0]["bindings"]["enter"].update(usage_id=-1),
            "boolean usage": lambda d: d["profiles"][0]["bindings"]["enter"].update(usage_id=True),
            "oversize usage": lambda d: d["profiles"][0]["bindings"]["enter"].update(usage_id=65536),
            "unknown modifier": lambda d: d["profiles"][0]["bindings"]["enter"].update(modifiers=["run-command"]),
            "duplicate modifier": lambda d: d["profiles"][0]["bindings"]["enter"].update(modifiers=["ctrl", "ctrl"]),
            "modifier object": lambda d: d["profiles"][0]["bindings"]["enter"].update(modifiers=[{}]),
            "unknown effect": lambda d: d["profiles"][0]["rgb"].update(effect="send-bytes"),
            "bad color": lambda d: d["profiles"][0]["rgb"].update(color="red"),
            "bad brightness": lambda d: d["profiles"][0]["rgb"].update(brightness=101),
            "boolean brightness": lambda d: d["profiles"][0]["rgb"].update(brightness=True),
            "fraction speed": lambda d: d["profiles"][0]["rgb"].update(speed=50.5),
            "unknown per-key color": lambda d: d["profiles"][0]["rgb"].update(per_key={"unknown": "#abcdef"}),
            "invalid per-key color": lambda d: d["profiles"][0]["rgb"].update(per_key={"enter": "#abcd"}),
            "missing device settings": lambda d: d["profiles"][0]["rgb"].pop("device_settings"),
            "unknown device setting": lambda d: d["profiles"][0]["rgb"]["device_settings"].update(unknown=1),
            "missing device setting": lambda d: d["profiles"][0]["rgb"]["device_settings"].pop("sensitivity"),
        }
        for description, modify in modifications.items():
            with self.subTest(description=description):
                document = profiles.new_document()
                modify(document)
                with self.assertRaises(ValueError):
                    profiles.validate_document(document)

    def test_device_settings_use_strict_booleans_and_bounded_integers(self):
        invalid_values = {
            "num_lock_indicator": [0, 1, "false", None],
            "battery_indicator": [0, 1, "true", None],
            "sensitivity": [-1, 101, True, 2.5, "2"],
            "wireless_brightness": [-1, 101, False, 70.5, "70"],
        }
        for field, values in invalid_values.items():
            for value in values:
                document = profiles.new_document()
                document["profiles"][0]["rgb"]["device_settings"][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    profiles.validate_document(document)
        for value in (0, 100):
            document = profiles.new_document()
            document["profiles"][0]["rgb"]["device_settings"].update(sensitivity=value, wireless_brightness=value)
            profiles.validate_document(document)

    def test_valid_v1_load_migrates_defaults_with_each_profile_brightness(self):
        legacy = self.legacy_document()
        legacy["profiles"][0]["rgb"]["brightness"] = 13
        legacy["profiles"][1]["rgb"]["brightness"] = 0
        legacy["profiles"][2]["rgb"]["brightness"] = 100
        legacy["profiles"][1]["rgb"]["per_key"] = {"enter": "#abcdef"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v1.json"
            original_json = json.dumps(legacy)
            path.write_text(original_json)
            migrated = profiles.load_document(path)
            self.assertEqual(path.read_text(), original_json)
            self.assertEqual(migrated["schema_version"], 2)
            for index, brightness in enumerate((13, 0, 100)):
                self.assertEqual(migrated["profiles"][index]["rgb"]["device_settings"], {
                    "num_lock_indicator": False, "battery_indicator": True,
                    "sensitivity": 2, "wireless_brightness": brightness,
                })
            self.assertEqual(migrated["profiles"][1]["rgb"]["per_key"], {"enter": "#abcdef"})
            profiles.validate_document(migrated)
            profiles.save_document(path, migrated)
            self.assertEqual(profiles.load_document(path), migrated)

    def test_invalid_v1_is_rejected_before_migration_can_hide_fields(self):
        modifications = {
            "unknown root field": lambda d: d.update(unverified=True),
            "unknown profile field": lambda d: d["profiles"][0].update(unverified=True),
            "unknown RGB field": lambda d: d["profiles"][0]["rgb"].update(unverified=True),
            "v1 cannot contain device settings": lambda d: d["profiles"][0]["rgb"].update(device_settings={}),
            "unknown target field": lambda d: d["target"].update(unverified=True),
            "invalid brightness": lambda d: d["profiles"][0]["rgb"].update(brightness=True),
            "invalid usage": lambda d: d["profiles"][0]["bindings"]["enter"].update(usage_id=256),
            "missing RGB field": lambda d: d["profiles"][0]["rgb"].pop("speed"),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid-v1.json"
            for description, modify in modifications.items():
                legacy = self.legacy_document()
                modify(legacy)
                original_json = json.dumps(legacy)
                path.write_text(original_json)
                with self.subTest(description=description), self.assertRaises(ValueError):
                    profiles.load_document(path)
                self.assertEqual(path.read_text(), original_json)

    def test_v1_requires_load_migration_before_validation_or_save(self):
        legacy = self.legacy_document()
        with self.assertRaises(ValueError):
            profiles.validate_document(legacy)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unchanged.json"
            path.write_text("keep previous contents")
            with self.assertRaises(ValueError):
                profiles.save_document(path, legacy)
            self.assertEqual(path.read_text(), "keep previous contents")

    def test_profile_count_is_local_limit_not_hardware_slot_count(self):
        document = profiles.new_document()
        template = document["profiles"][0]
        document["profiles"] = [dict(copy.deepcopy(template), id=f"local-{i}") for i in range(32)]
        document["selected_profile_id"] = "local-0"
        profiles.validate_document(document)
        document["profiles"].append(dict(copy.deepcopy(template), id="local-32"))
        with self.assertRaises(ValueError):
            profiles.validate_document(document)

    def test_invalid_imports_reject_duplicates_nonfinite_and_oversized_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            for content in [b'{"schema_version":1,"schema_version":1}', b'{"x":NaN}', b'\xff', b'[]', b'{']:
                with self.subTest(content=content):
                    path.write_bytes(content)
                    with self.assertRaises(ValueError):
                        profiles.load_document(path)
            path.write_bytes(b" " * (profiles.MAX_DOCUMENT_BYTES + 1))
            with self.assertRaisesRegex(ValueError, "size limit"):
                profiles.load_document(path)

    def test_invalid_save_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "draft.json"
            path.write_text("previous contents")
            invalid = profiles.new_document()
            invalid["profiles"][0]["rgb"]["brightness"] = 200
            with self.assertRaises(ValueError):
                profiles.save_document(path, invalid)
            self.assertEqual(path.read_text(), "previous contents")
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_replace_failure_keeps_previous_file_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "draft.json"
            path.write_text("previous contents")
            with mock.patch.object(profiles.os, "replace", side_effect=OSError("simulated replacement failure")):
                with self.assertRaises(OSError):
                    profiles.save_document(path, profiles.new_document())
            self.assertEqual(path.read_text(), "previous contents")
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
