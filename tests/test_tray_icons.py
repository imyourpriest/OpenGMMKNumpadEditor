"""Keep every icon state exported to the desktop backed by a valid SVG asset."""
from pathlib import Path
import unittest
from xml.etree import ElementTree


TRAY_ICONS = Path(__file__).resolve().parents[1] / "assets" / "tray"


class TrayIconAssetTests(unittest.TestCase):
    def test_complete_battery_transport_and_unavailable_icon_set(self):
        expected = {f"gmmk-numpad-{transport}-{level:03d}-symbolic.svg"
                    for transport in ("usb", "bluetooth") for level in range(0, 101, 5)}
        expected.update(f"gmmk-numpad-{transport}-unavailable-symbolic.svg"
                        for transport in ("usb", "bluetooth"))
        expected.update(f"gmmk-numpad-{state}-symbolic.svg"
                        for state in ("offline", "ambiguous", "unknown"))
        self.assertEqual({path.name for path in TRAY_ICONS.glob("*.svg")}, expected)

    def test_all_tray_icons_are_valid_scalable_svg_documents(self):
        icons = sorted(TRAY_ICONS.glob("*.svg"))
        self.assertTrue(icons, "The bundled tray icon directory must not be empty")
        for path in icons:
            with self.subTest(icon=path.name):
                root = ElementTree.parse(path).getroot()
                self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
                viewbox = [float(value) for value in root.attrib["viewBox"].split()]
                self.assertEqual(len(viewbox), 4)
                self.assertGreater(viewbox[2], 0)
                self.assertGreater(viewbox[3], 0)


if __name__ == "__main__":
    unittest.main()
