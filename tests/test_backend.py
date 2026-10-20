"""Verify device sequencing and failure behavior without touching hardware."""
import unittest
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend
from profile import new_document


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.profile = new_document()['profiles'][0]
        self.device_patch = patch.object(backend, 'connected_device', return_value={'test': True})
        self.transport_patch = patch.object(backend, 'StockTransport')
        self.sleep_patch = patch.object(backend.time, 'sleep')
        self.device_patch.start()
        self.transport = self.transport_patch.start()
        self.sleep_patch.start()
        self.addCleanup(self.device_patch.stop)
        self.addCleanup(self.transport_patch.stop)
        self.addCleanup(self.sleep_patch.stop)
        self.connection = self.transport.return_value.__enter__.return_value

    def commands(self):
        return [call.args[0][1] for call in self.connection.send_feature.call_args_list]

    def test_bad_key_draft_opens_no_hardware(self):
        self.profile['bindings']['num_lock']['usage_id'] = 1000
        with self.assertRaises(ValueError):
            backend.write_bindings(self.profile, 1, 1)
        self.transport.assert_not_called()

    def test_key_write_only_uses_keymap_and_activation(self):
        backend.write_bindings(self.profile, 2, 3)
        self.assertEqual(self.commands(), [3, 1])
        for call in self.connection.send_feature.call_args_list:
            self.assertEqual(call.args[0][2:4], b'\x02\x03')

    def test_failed_key_write_does_not_select_or_retry(self):
        self.connection.send_feature.side_effect = OSError('disconnected')
        with self.assertRaises(OSError):
            backend.write_bindings(self.profile, 1, 1)
        self.assertEqual(self.commands(), [3])
        self.transport.return_value.__exit__.assert_called_once()

    def test_lighting_sequence_and_no_keymap_write(self):
        self.profile['rgb']['per_key']['num_lock'] = '#ff0000'
        backend.write_lighting(self.profile, 1, 2, dict(num_lock_indicator=False,
            battery_indicator=True, sensitivity=2, wireless_brightness=70))
        self.assertEqual(self.commands(), [2, 7, 6, 1])

    def test_incomplete_lighting_settings_open_no_hardware(self):
        with self.assertRaises(TypeError):
            backend.write_lighting(self.profile, 1, 1, {})
        self.transport.assert_not_called()

    def test_no_overrides_still_clears_previous_color_layer(self):
        backend.write_lighting(self.profile, 1, 1, dict(num_lock_indicator=False,
            battery_indicator=True, sensitivity=2, wireless_brightness=70))
        self.assertEqual(self.commands(), [2, 7, 6, 1])

    def test_status_query_never_sends_configuration(self):
        raw = bytearray(264)
        raw[0] = 7; raw[2] = 83; raw[4:6] = b'\x12\x00'
        self.connection.get_feature.return_value = bytes(raw)
        self.assertEqual(backend.read_status()['battery'], 83)
        self.assertEqual(self.commands(), [0x8a])


if __name__ == '__main__':
    unittest.main()
