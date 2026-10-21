"""Indicator tests do not connect to a desktop, HID device or session bus."""
from dataclasses import dataclass
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import status_notifier as tray


@dataclass
class Snapshot:
    transport: str = "usb"
    battery_percent: int | None = 87
    charging: bool | None = None
    timestamp: float = 1234567890
    stale: bool = False
    error: str | None = None
    usb_connected: bool = True
    bluetooth_connected: bool = False


class FakeVariant:
    def __init__(self, signature, value):
        self.signature, self.value = signature, value

    def unpack(self):
        return self.value

    @classmethod
    def new_tuple(cls, *children):
        return cls("(" + "".join(child.signature for child in children) + ")", children)


class FakeGLib:
    Variant = FakeVariant

    class VariantType:
        @staticmethod
        def new(signature):
            return signature

    def __init__(self):
        self.sources = {}
        self.next_id = 1
        self.delays = []
        self.removed = []

    def idle_add(self, callback):
        current = self.next_id
        self.next_id += 1
        self.sources[current] = callback
        return current

    def timeout_add_seconds(self, delay, callback):
        self.delays.append(delay)
        return self.idle_add(callback)

    def source_remove(self, source):
        self.removed.append(source)
        self.sources.pop(source, None)

    def drain(self):
        callbacks, self.sources = self.sources, {}
        for callback in callbacks.values():
            callback()


class FakeGio:
    class DBusCallFlags:
        NO_AUTO_START = 1


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.signals = []
        self.unregistered = []

    def call(self, *args):
        self.calls.append(args)

    def call_finish(self, result):
        if isinstance(result, Exception):
            raise result
        return result

    def is_closed(self):
        return False

    def emit_signal(self, *args):
        self.signals.append(args)


class Invocation:
    def return_value(self, value):
        self.value = value

    def return_dbus_error(self, name, message):
        self.error = name, message


class SnapshotDescriptionTests(unittest.TestCase):
    def test_live_usb_battery_is_visible_and_charging_is_not_guessed(self):
        result = tray.describe_snapshot(Snapshot())
        self.assertEqual(result["label"], "USB 87%")
        self.assertEqual(result["battery"], "Battery: 87%")
        self.assertEqual(result["connection"], "USB connected")

    def test_bluetooth_and_zero_percent_are_visible(self):
        result = tray.describe_snapshot(Snapshot(transport="bluetooth", battery_percent=0,
                                               usb_connected=False, bluetooth_connected=True))
        self.assertEqual(result["label"], "Bluetooth 0%")
        self.assertEqual(result["overlay"], "bluetooth-active-symbolic")

    def test_stale_reading_never_looks_current_in_topbar(self):
        result = tray.describe_snapshot(Snapshot(stale=True))
        self.assertEqual(result["label"], "USB")
        self.assertIn("last reading", result["battery"])

    def test_unknown_or_invalid_percent_remains_unknown(self):
        for percent in (None, True, -1, 101, 0.5, "80"):
            with self.subTest(percent=percent):
                result = tray.describe_snapshot(Snapshot(battery_percent=percent))
                self.assertEqual(result["battery"], "Battery unavailable")
                self.assertEqual(result["label"], "USB")

    def test_disconnected_and_ambiguous_are_explicit(self):
        self.assertEqual(tray.describe_snapshot(None)["label"], "Offline")
        self.assertEqual(tray.describe_snapshot({"transport": "ambiguous"})["connection"],
                         "Multiple numpads detected")

    def test_both_links_do_not_claim_active_keyboard_input_route(self):
        result = tray.describe_snapshot(Snapshot(bluetooth_connected=True))
        self.assertEqual(result["connection"], "USB and Bluetooth connected")

    def test_tooltip_escapes_markup_and_bounds_single_line_error(self):
        result = tray.describe_snapshot(Snapshot(error="<bad>\n" + "x" * 300))
        self.assertIn("&lt;bad&gt;", result["tooltip"])
        self.assertLessEqual(len(result["error"]), 220)
        self.assertNotIn("\n", result["error"])

    def test_charging_only_uses_explicit_boolean(self):
        self.assertIn("· charging", tray.describe_snapshot(Snapshot(charging=True))["battery"])
        self.assertIn("· not charging", tray.describe_snapshot(Snapshot(charging=False))["battery"])

    def test_invalid_timestamp_does_not_break_status_update(self):
        for stamp in (None, 0, "bad", 1e100):
            self.assertEqual(tray.describe_snapshot(Snapshot(timestamp=stamp))["updated"], "Not checked yet")


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.actions = []
        self.model = tray.MenuModel(lambda: self.actions.append("open"),
                                   lambda: self.actions.append("refresh"),
                                   lambda: self.actions.append("quit"))

    def test_updates_preserve_ids_callbacks_and_disable_status_rows(self):
        before = list(self.model.rows)
        callbacks = dict(self.model.callbacks)
        self.model.update(Snapshot())
        self.model.update(Snapshot(transport="bluetooth"))
        self.assertEqual(list(self.model.rows), before)
        self.assertEqual(self.model.callbacks, callbacks)
        for key in (self.model.CONNECTION, self.model.BATTERY, self.model.UPDATED, self.model.ERROR):
            self.assertFalse(self.model.rows[key]["enabled"])

    def test_revision_changes_only_when_menu_changes(self):
        self.model.update(Snapshot())
        before = self.model.revision
        self.assertFalse(self.model.update(Snapshot()))
        self.assertEqual(self.model.revision, before)
        self.assertTrue(self.model.update(Snapshot(battery_percent=50)))
        self.assertEqual(self.model.revision, before + 1)

    def test_layout_depth_and_property_filter(self):
        self.assertEqual(self.model.layout(depth=0)[2], [])
        tree = self.model.layout(requested=["label"])
        self.assertEqual(len(tree[2]), 8)
        self.assertTrue(all(set(properties) <= {"label"} for _, properties, _ in tree[2]))

    def test_only_click_events_invoke_enabled_actions(self):
        self.assertIsNone(self.model.callback(self.model.OPEN, "hovered"))
        self.assertIsNone(self.model.callback(self.model.BATTERY, "clicked"))
        self.model.callback(self.model.OPEN, "clicked")()
        self.assertEqual(self.actions, ["open"])
        with self.assertRaises(KeyError):
            self.model.callback(999, "clicked")

    def test_error_row_visibility_does_not_change_ids(self):
        self.assertFalse(self.model.rows[self.model.ERROR]["visible"])
        self.model.update(Snapshot(error="USB access unavailable"))
        self.assertTrue(self.model.rows[self.model.ERROR]["visible"])


class BusPresentationTests(unittest.TestCase):
    def setUp(self):
        self.glib = FakeGLib()
        self.actions = []
        self.indicator = tray.StatusNotifier(lambda: self.actions.append("open"),
                                             lambda: self.actions.append("refresh"),
                                             lambda: self.actions.append("quit"),
                                             _gi=(FakeGio, self.glib))
        self.indicator.Gio, self.indicator.GLib = FakeGio, self.glib
        self.connection = FakeConnection()

    def invoke(self, method, args):
        invocation = Invocation()
        self.indicator._menu_method(None, None, None, None, method, FakeVariant("", args), invocation)
        return invocation

    def test_start_without_gi_is_optional_and_does_not_raise(self):
        with patch.object(tray, "_load_gi", side_effect=ImportError("not installed")):
            indicator = tray.StatusNotifier(lambda: None, lambda: None, lambda: None)
            self.assertFalse(indicator.start())
            self.assertFalse(indicator.registered)
            self.assertIn("not installed", indicator.last_error)

    def test_exported_xml_has_required_property_and_menu_signatures(self):
        item = ElementTree.fromstring(tray.ITEM_XML).find("interface")
        self.assertEqual(item.get("name"), tray.ITEM_INTERFACE)
        properties = {node.get("name"): node.get("type") for node in item.findall("property")}
        self.assertEqual(properties["ToolTip"], "(sa(iiay)ss)")
        self.assertEqual(properties["Menu"], "o")
        self.assertEqual(properties["XAyatanaLabel"], "s")
        menu = ElementTree.fromstring(tray.MENU_XML).find("interface")
        methods = {method.get("name") for method in menu.findall("method")}
        self.assertTrue({"GetLayout", "GetProperty", "GetGroupProperties", "Event", "EventGroup",
                         "AboutToShow", "AboutToShowGroup"} <= methods)

    def test_property_types_include_menu_and_visible_transport_label(self):
        self.indicator.update(Snapshot())
        self.assertEqual(self.indicator._item_property("XAyatanaLabel").value, "USB 87%")
        self.assertEqual(self.indicator._item_property("Menu").signature, "o")
        self.assertEqual(self.indicator._item_property("ItemIsMenu").value, True)

    def test_identical_updates_emit_no_signals(self):
        self.indicator._connection = self.connection
        self.indicator.update(Snapshot())
        self.connection.signals.clear()
        self.indicator.update(Snapshot())
        self.assertEqual(self.connection.signals, [])

    def test_changed_status_notifies_menu_label_and_tooltip(self):
        self.indicator._connection = self.connection
        self.indicator.update(Snapshot())
        signals = [args[3] for args in self.connection.signals]
        self.assertIn("LayoutUpdated", signals)
        self.assertIn("PropertiesChanged", signals)
        self.assertIn("XAyatanaNewLabel", signals)
        self.assertIn("NewToolTip", signals)

    def test_layout_keeps_variant_properties_when_wrapped(self):
        result = self.invoke("GetLayout", (0, -1, []))
        self.assertEqual(result.value.signature, "(u(ia{sv}av))")
        self.assertIsInstance(result.value.value[1].value[1]["children-display"], FakeVariant)

    def test_event_callbacks_run_later_not_inside_bus_handler(self):
        self.invoke("Event", (self.indicator.menu.REFRESH, "clicked", "", 0))
        self.assertEqual(self.actions, [])
        self.glib.drain()
        self.assertEqual(self.actions, ["refresh"])

    def test_bad_eventgroup_ids_report_errors_without_losing_valid_actions(self):
        response = self.invoke("EventGroup", ([(999, "clicked", "", 0), (1, "clicked", "", 0)],))
        self.assertEqual(response.value.value, ([999],))
        self.glib.drain()
        self.assertEqual(self.actions, ["open"])

    def test_unknown_menu_property_returns_dbus_error(self):
        invocation = self.invoke("GetProperty", (999, "label"))
        self.assertEqual(invocation.error[0], "com.canonical.dbusmenu.Error.InvalidMenuItem")

    def test_retry_is_bounded_and_never_accumulates_timers(self):
        self.indicator._started = True
        with patch.object(self.indicator, "_connect"):
            self.indicator._schedule_retry()
            self.indicator._schedule_retry()
            self.assertEqual(self.glib.delays, [3])
            for _ in range(5):
                self.glib.drain()
                self.indicator._schedule_retry()
        self.assertEqual(self.glib.delays, [3, 10, 30, 60, 60, 60])

    def test_registration_waits_for_active_host_and_current_owner(self):
        item = self.indicator
        item._started = item._name_owned = True
        item._connection = self.connection
        item._selected = (tray.WATCHERS[0], ":1.9")
        item._register()
        self.assertEqual(self.connection.calls[-1][3], "Get")
        context = self.connection.calls[-1][-1]
        item._host_checked(self.connection, FakeVariant("(v)", (False,)), context)
        self.assertFalse(item.registered)
        self.assertEqual(len(self.connection.calls), 1)
        item._register()
        item._host_checked(self.connection, FakeVariant("(v)", (True,)), context)
        self.assertEqual(self.connection.calls[-1][3], "RegisterStatusNotifierItem")
        item._registered(self.connection, None, context)
        self.assertTrue(item.registered)

    def test_late_reply_from_replaced_watcher_cannot_restore_registration(self):
        item = self.indicator
        item._started = True
        item._connection = self.connection
        item._selected = (tray.WATCHERS[0], ":1.10")
        old = (self.connection, (tray.WATCHERS[0], ":1.9"), item._registration_generation)
        item._registered(self.connection, None, old)
        self.assertFalse(item.registered)

    def test_aliases_register_only_one_watcher(self):
        item = self.indicator
        item._started = item._name_owned = True
        item._connection = self.connection
        item._watcher_owners = {name: ":1.9" for name in tray.WATCHERS}
        item._choose_watcher()
        self.assertEqual(item._selected[0], tray.WATCHERS[0])
        self.assertEqual(len(self.connection.calls), 1)

    def test_registration_callback_only_runs_when_state_changes(self):
        changed = []
        self.indicator.on_registration_changed = changed.append
        self.indicator._set_registered(True)
        self.indicator._set_registered(True)
        self.indicator._set_registered(False)
        self.assertEqual(changed, [])
        self.glib.drain()
        self.assertEqual(changed, [True, False])


if __name__ == "__main__":
    unittest.main()
