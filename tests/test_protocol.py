"""Golden wire frames and fail-closed boundaries, without device access."""

from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import profile
import protocol


class ProtocolTests(unittest.TestCase):
    def matching(self):
        return {
            "vendor": "320f", "product": "5088", "revision": "0088", "supported_identity": True,
            "interfaces": [{
                "number": "02", "node": "/dev/hidraw999", "node_available": True,
                "node_is_character": True, "readable": True, "writable": True,
                "descriptor_sha256": protocol.FEATURE_DESCRIPTOR_EVIDENCE["descriptor_sha256"],
                "reports": [{"report_type": "feature", "report_id": 7,
                             "data_bits": 2104, "data_bytes": 263, "total_bytes": 264}],
            }],
        }

    def draft(self):
        return profile.new_document()["profiles"][0]

    def test_profile_readback_remains_unavailable(self):
        for device in [None, {}, self.matching(), dict(self.matching(), revision="0099")]:
            with self.subTest(device=device):
                capabilities = protocol.capabilities(device)
                self.assertIs(capabilities["can_read_device_profiles"], False)
                self.assertTrue(capabilities["reason"])

    def test_write_capability_requires_exact_live_metadata(self):
        self.assertTrue(protocol.capabilities(self.matching())["can_write_device"])
        for field, invalid in [("number", "01"), ("descriptor_sha256", "bad"),
                               ("node_available", False), ("node_is_character", False),
                               ("readable", False), ("writable", False)]:
            device = self.matching()
            device["interfaces"][0][field] = invalid
            with self.subTest(field=field):
                self.assertFalse(protocol.capabilities(device)["can_write_device"])
        device = self.matching()
        device["interfaces"].append(dict(device["interfaces"][0]))
        self.assertFalse(protocol.capabilities(device)["can_write_device"])
        for device in [None, {}, dict(self.matching(), revision="0099")]:
            self.assertFalse(protocol.capabilities(device)["can_write_device"])

    def test_imported_claims_do_not_enable_transport(self):
        device = {"verified": True, "can_write_device": True, "backend": "external"}
        self.assertFalse(protocol.capabilities(device)["can_write_device"])
        with self.assertRaises(protocol.ProtocolUnavailable):
            protocol.apply_to_device(profile.new_document(), device)

    def test_apply_always_raises_without_opening_any_file(self):
        document = profile.new_document()
        with mock.patch("builtins.open", side_effect=AssertionError("No file or device may be opened")), mock.patch("os.open", side_effect=AssertionError("No device may be opened")):
            for device in [None, {}, self.matching()]:
                with self.subTest(device=device):
                    with self.assertRaises(protocol.ProtocolUnavailable):
                        protocol.apply_to_device(document, device)

    def test_evidence_records_exact_descriptor(self):
        evidence = protocol.FEATURE_DESCRIPTOR_EVIDENCE
        self.assertEqual(evidence["interface"], "02")
        self.assertEqual(evidence["report_id"], 7)
        self.assertEqual(evidence["report_count"], 263)
        self.assertEqual(evidence["total_bytes"], evidence["payload_bytes"] + 1)
        self.assertNotIn("commands", evidence)

    def test_status_request_contains_only_documented_command(self):
        self.assertEqual(protocol.build_status_request(), b"\x07\x8a" + bytes(262))

    def test_raw_status_offsets_and_undocumented_values(self):
        raw = bytearray(264)
        raw[:6] = bytes.fromhex("07 8a 64 02 cd ab")
        self.assertEqual(protocol.parse_status(raw), {
            "response_code": 0x8A, "battery": 100, "charging": 2, "firmware_word": 0xABCD,
            "firmware_version": "abcd",
        })

    def test_bad_status_responses_are_rejected(self):
        valid = bytearray(protocol.build_status_request())
        for invalid in [b"", valid[1:], valid + b"\0", list(valid)]:
            with self.assertRaises(ValueError):
                protocol.parse_status(invalid)
        for index, value in [(0, 4), (2, 101)]:
            invalid = bytearray(valid)
            invalid[index] = value
            with self.subTest(index=index), self.assertRaises(ValueError):
                protocol.parse_status(invalid)

    def test_status_response_code_is_diagnostic_not_an_undocumented_echo_requirement(self):
        raw = bytearray(protocol.build_status_request())
        raw[1] = 0
        self.assertEqual(protocol.parse_status(raw)["response_code"], 0)

    def test_factory_table_golden_bytes(self):
        expected = bytes.fromhex(
            "07 03 01 01 00 00 00 00 "
            "01 01 01 01 01 01 01 01 01 01 01 03 01 01 01 01 01 00 00 01 "
            "53 54 55 56 5f 60 61 57 5c 5d 5e 10 59 5a 5b 58 62 00 00 63"
        ) + bytes(216)
        self.assertEqual(protocol.build_keymap(self.draft()), expected)

    def test_num_lock_to_print_screen_changes_only_one_table_byte(self):
        item = self.draft()
        original = protocol.build_keymap(item)
        item["bindings"]["num_lock"]["usage_id"] = 0x46
        actual = protocol.build_keymap(item)
        self.assertEqual([(i, a, b) for i, (a, b) in enumerate(zip(original, actual)) if a != b],
                         [(28, 0x53, 0x46)])

    def test_modifier_mask_and_all_combinations(self):
        for mask in range(16):
            item = self.draft()
            binding = item["bindings"]["kp_1"]
            binding["usage_id"] = 4
            binding["modifiers"] = [name for name, bit in protocol.MODIFIER_BITS.items() if mask & bit]
            actual = protocol.build_keymap(item, 3, 2)
            self.assertEqual(actual[2:4], b"\x03\x02")
            self.assertEqual(actual[8 + 12], 0xE0 + mask if mask else 1)
            self.assertEqual(actual[28 + 12], 4)

    def test_encoder_and_matrix_holes_preserved_when_all_keys_change(self):
        item = self.draft()
        for binding in item["bindings"].values():
            binding.update(usage_id=0xFF, modifiers=["super"])
        actual = protocol.build_keymap(item)
        self.assertEqual(actual[8 + 11], 3)
        self.assertEqual(actual[28 + 11], 0x10)
        for index in (17, 18):
            self.assertEqual((actual[8 + index], actual[28 + index]), (0, 0))
        self.assertEqual(actual[48:], bytes(216))
        self.assertEqual(len(protocol.KEY_INDICES), 17)

    def test_invalid_usage_and_modifier_are_rejected(self):
        for value in [-1, 256, True, "4"]:
            item = self.draft()
            item["bindings"]["enter"]["usage_id"] = value
            with self.subTest(usage=value), self.assertRaises(ValueError):
                protocol.build_keymap(item)
        for modifiers in [["right_ctrl"], ["left_alt"], ["ctrl", "ctrl"], ["fn"]]:
            item = self.draft()
            item["bindings"]["enter"]["modifiers"] = modifiers
            with self.subTest(modifiers=modifiers), self.assertRaises(ValueError):
                protocol.build_keymap(item)

    def test_invalid_profile_documents_are_rejected(self):
        for edit in [lambda p: p["bindings"].pop("divide"),
                     lambda p: p.update(unverified=True),
                     lambda p: p["bindings"]["enter"].update(usage_page=12)]:
            item = self.draft()
            edit(item)
            with self.assertRaises(ValueError):
                protocol.build_keymap(item)

    def test_profile_selection_and_bounds(self):
        self.assertEqual(protocol.build_select_profile(3, 2), bytes.fromhex("07 01 03 02") + bytes(260))
        for invalid in [0, 4, -1, True, "1"]:
            for builder in [lambda: protocol.build_select_profile(invalid, 1),
                            lambda: protocol.build_select_profile(1, invalid),
                            lambda: protocol.build_keymap(self.draft(), invalid, 1),
                            lambda: protocol.build_keymap(self.draft(), 1, invalid)]:
                with self.assertRaises(ValueError):
                    builder()

    def test_static_rgb_effect_golden_bytes(self):
        rgb = self.draft()["rgb"]
        expected = bytearray(264)
        expected[:5] = bytes.fromhex("07 02 01 01 07")
        expected[8] = 10  # trunc(20 - 50 * 19 / 100)
        expected[15:18] = bytes.fromhex("33 aa ff")
        self.assertEqual(protocol.build_rgb_effect(rgb), expected)

    def test_effect_ids_and_speed_endpoints(self):
        expected_ids = {"static": 7, "breathing": 6, "rainbow": 13, "wave": 2, "reactive": 8, "off": 0}
        for name, effect_id in expected_ids.items():
            for speed, encoded in [(0, 20), (100, 1)]:
                rgb = self.draft()["rgb"]
                rgb.update(effect=name, speed=speed)
                actual = protocol.build_rgb_effect(rgb, 3, 2)
                self.assertEqual(actual[2:5], bytes((3, 2, effect_id)))
                self.assertEqual(actual[8], encoded)
                self.assertEqual(actual[12], int(name == "rainbow"))
                self.assertEqual(actual[10], 0)

    def test_rgb_settings_golden_and_off_brightness(self):
        rgb = self.draft()["rgb"]
        options = dict(num_lock_indicator=False, battery_indicator=True, sensitivity=2, wireless_brightness=100)
        expected = bytearray(264)
        expected[:5] = bytes.fromhex("07 07 01 01 07")
        expected[6:14] = bytes((1, 0, 14, 0, 8, 1, 1, 20))
        self.assertEqual(protocol.build_rgb_settings(rgb, **options), expected)
        rgb["effect"] = "off"
        actual = protocol.build_rgb_settings(rgb, **options)
        self.assertEqual((actual[4], actual[8], actual[13]), (0, 10, 10))
        with self.assertRaises(TypeError):
            protocol.build_rgb_settings(rgb)
        for field, value in [("sensitivity", -1), ("wireless_brightness", 101), ("battery_indicator", 1)]:
            invalid = dict(options, **{field: value})
            with self.assertRaises(ValueError):
                protocol.build_rgb_settings(rgb, **invalid)

    def test_per_key_rgb_complete_layout_holes_and_overrides(self):
        rgb = self.draft()["rgb"]
        rgb["per_key"] = {"num_lock": "#ff0000", "decimal": "#010203"}
        actual = protocol.build_per_key_rgb(rgb, 2, 3)
        self.assertEqual(actual[:8], bytes.fromhex("07 06 02 03 2a 00 0e 00"))
        expected_indices = set(protocol.SIDE_LED_INDICES) | set(protocol.KEY_LED_INDICES.values())
        self.assertEqual(len(expected_indices), 31)
        for index in range(42):
            record = actual[8 + 5 * index:13 + 5 * index]
            if index in expected_indices:
                if index in (1, 39):
                    color = bytes.fromhex("ff0000" if index == 1 else "010203")
                    self.assertEqual(record, bytes((255, index)) + color)
                else:
                    self.assertEqual(record, bytes((0, index, 0, 0, 0)))
            else:
                self.assertEqual(record, bytes(5))
        self.assertEqual(actual[218:], bytes(46))

    def test_no_per_key_overrides_clears_prior_palette_without_masking_global_effect(self):
        rgb = self.draft()["rgb"]
        rgb["effect"] = "rainbow"
        actual = protocol.build_per_key_rgb(rgb)
        valid_indices = set(protocol.SIDE_LED_INDICES) | set(protocol.KEY_LED_INDICES.values())
        for index in range(42):
            expected = bytes((0, index, 0, 0, 0)) if index in valid_indices else bytes(5)
            self.assertEqual(actual[8 + index * 5:13 + index * 5], expected)

    def test_off_clears_even_explicit_per_key_overrides(self):
        rgb = self.draft()["rgb"]
        rgb.update(effect="off", per_key={"num_lock": "#ff0000", "kp_0": "#ffffff"})
        actual = protocol.build_per_key_rgb(rgb)
        rgb["per_key"] = {}
        self.assertEqual(actual, protocol.build_per_key_rgb(rgb))
        self.assertTrue(all(actual[8 + index * 5] == 0 for index in range(42)))

    def test_builders_have_no_io_or_mutations(self):
        item = self.draft()
        import copy
        before = copy.deepcopy(item)
        with mock.patch("builtins.open", side_effect=AssertionError("No IO")), mock.patch("os.open", side_effect=AssertionError("No IO")):
            frames = [protocol.build_status_request(), protocol.build_keymap(item),
                      protocol.build_select_profile(), protocol.build_rgb_effect(item["rgb"]),
                      protocol.build_per_key_rgb(item["rgb"])]
        self.assertTrue(all(type(frame) is bytes and len(frame) == 264 for frame in frames))
        self.assertEqual([frame[1] for frame in frames], [0x8A, 3, 1, 2, 6])
        self.assertEqual(item, before)


if __name__ == "__main__":
    unittest.main()
