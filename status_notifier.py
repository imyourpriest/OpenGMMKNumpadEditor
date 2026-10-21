"""Session-bus tray indicator, without an AppIndicator library dependency.

The deployed KDE StatusNotifierItem interface and Canonical D-BusMenu interface
are supported by Ubuntu's AppIndicator extension. This module only presents a
monitor snapshot. It never opens the numpad or changes its configuration.

Reference interfaces:
https://specifications.freedesktop.org/status-notifier-item/latest-single/
https://github.com/ubuntu/gnome-shell-extension-appindicator/tree/master/interfaces-xml
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from html import escape
import logging
import os


APP_ID = "io.github.opengmmk.NumpadEditor"
APP_NAME = "OpenGMMK Numpad Editor"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
ITEM_INTERFACE = "org.kde.StatusNotifierItem"
MENU_INTERFACE = "com.canonical.dbusmenu"
WATCHER_PATH = "/StatusNotifierWatcher"
WATCHERS = ("org.kde.StatusNotifierWatcher", "org.freedesktop.StatusNotifierWatcher")
LOG = logging.getLogger(__name__)


ITEM_XML = """<node><interface name="org.kde.StatusNotifierItem">
 <property name="Category" type="s" access="read"/>
 <property name="Id" type="s" access="read"/>
 <property name="Title" type="s" access="read"/>
 <property name="Status" type="s" access="read"/>
 <property name="WindowId" type="i" access="read"/>
 <property name="IconThemePath" type="s" access="read"/>
 <property name="IconName" type="s" access="read"/>
 <property name="IconPixmap" type="a(iiay)" access="read"/>
 <property name="OverlayIconName" type="s" access="read"/>
 <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
 <property name="AttentionIconName" type="s" access="read"/>
 <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
 <property name="AttentionMovieName" type="s" access="read"/>
 <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
 <property name="ItemIsMenu" type="b" access="read"/>
 <property name="Menu" type="o" access="read"/>
 <property name="XAyatanaLabel" type="s" access="read"/>
 <property name="XAyatanaLabelGuide" type="s" access="read"/>
 <method name="Activate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
 <method name="SecondaryActivate"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
 <method name="XAyatanaSecondaryActivate"><arg type="u" direction="in"/></method>
 <method name="ContextMenu"><arg type="i" direction="in"/><arg type="i" direction="in"/></method>
 <method name="Scroll"><arg type="i" direction="in"/><arg type="s" direction="in"/></method>
 <method name="ProvideXdgActivationToken"><arg type="s" direction="in"/></method>
 <signal name="NewTitle"/><signal name="NewIcon"/><signal name="NewOverlayIcon"/>
 <signal name="NewToolTip"/><signal name="NewStatus"><arg type="s"/></signal>
 <signal name="XAyatanaNewLabel"><arg type="s"/><arg type="s"/></signal>
</interface></node>"""

MENU_XML = """<node><interface name="com.canonical.dbusmenu">
 <property name="Version" type="u" access="read"/>
 <property name="TextDirection" type="s" access="read"/>
 <property name="Status" type="s" access="read"/>
 <property name="IconThemePath" type="as" access="read"/>
 <method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/>
  <arg type="as" direction="in"/><arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method>
 <method name="GetGroupProperties"><arg type="ai" direction="in"/><arg type="as" direction="in"/>
  <arg type="a(ia{sv})" direction="out"/></method>
 <method name="GetProperty"><arg type="i" direction="in"/><arg type="s" direction="in"/>
  <arg type="v" direction="out"/></method>
 <method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/>
  <arg type="v" direction="in"/><arg type="u" direction="in"/></method>
 <method name="EventGroup"><arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/></method>
 <method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
 <method name="AboutToShowGroup"><arg type="ai" direction="in"/><arg type="ai" direction="out"/>
  <arg type="ai" direction="out"/></method>
 <signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})"/><arg type="a(ias)"/></signal>
 <signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
 <signal name="ItemActivationRequested"><arg type="i"/><arg type="u"/></signal>
</interface></node>"""


def _field(snapshot, name, default=None):
    if isinstance(snapshot, Mapping):
        return snapshot.get(name, default)
    return getattr(snapshot, name, default)


def describe_snapshot(snapshot):
    """Return bounded plain labels; unknown and stale data stay distinguishable."""
    transport = _field(snapshot, "transport", _field(snapshot, "connection", "disconnected"))
    connections = {"usb": "USB connected", "bluetooth": "Bluetooth connected",
                   "disconnected": "Not connected", "ambiguous": "Multiple numpads detected"}
    connection = connections.get(transport, "Connection unknown")
    if _field(snapshot, "usb_connected", False) and _field(snapshot, "bluetooth_connected", False):
        connection = "USB and Bluetooth connected"
    short = {"usb": "USB", "bluetooth": "Bluetooth", "disconnected": "Offline",
             "ambiguous": "Multiple"}.get(transport, "Unknown")
    percent = _field(snapshot, "battery_percent")
    if type(percent) is not int or not 0 <= percent <= 100:
        percent = None
    stale = bool(_field(snapshot, "stale", False))
    charging = _field(snapshot, "charging")
    battery = "Battery unavailable" if percent is None else f"Battery: {percent}%"
    if percent is not None and stale:
        battery += " (last reading)"
    elif charging is True:
        battery += " · charging"
    elif charging is False:
        battery += " · not charging"
    label = short
    if percent is not None and not stale:
        label += f" {percent}%"
    stamp = _field(snapshot, "timestamp", _field(snapshot, "updated_at", 0))
    try:
        updated = ("Updated: " + datetime.fromtimestamp(stamp).strftime("%H:%M:%S")) if stamp else "Not checked yet"
    except (ValueError, TypeError, OverflowError, OSError):
        updated = "Not checked yet"
    error = str(_field(snapshot, "error") or "").replace("\n", " ")[:220]
    return {"connection": connection, "battery": battery, "updated": updated,
            "error": error, "label": label,
            "overlay": "bluetooth-active-symbolic" if transport == "bluetooth" else "",
            "tooltip": "\n".join(escape(s) for s in (connection, battery, updated, error) if s)}


class MenuModel:
    """Stable menu IDs and callbacks survive every monitoring refresh."""

    OPEN, CONNECTION, BATTERY, UPDATED, ERROR, SEPARATOR, REFRESH, QUIT = range(1, 9)

    def __init__(self, on_open, on_refresh, on_quit):
        self.callbacks = {self.OPEN: on_open, self.REFRESH: on_refresh, self.QUIT: on_quit}
        self.revision = 1
        self.rows = {}
        self.update(None)

    def update(self, snapshot):
        text = describe_snapshot(snapshot)
        rows = {
            0: {"children-display": "submenu"},
            self.OPEN: {"label": "Open editor", "enabled": True, "visible": True},
            self.CONNECTION: {"label": text["connection"], "enabled": False, "visible": True},
            self.BATTERY: {"label": text["battery"], "enabled": False, "visible": True},
            self.UPDATED: {"label": text["updated"], "enabled": False, "visible": True},
            self.ERROR: {"label": text["error"], "enabled": False, "visible": bool(text["error"])},
            self.SEPARATOR: {"type": "separator", "visible": True},
            self.REFRESH: {"label": "Refresh status", "enabled": True, "visible": True},
            self.QUIT: {"label": "Quit", "enabled": True, "visible": True},
        }
        if rows == self.rows:
            return False
        self.rows = rows
        self.revision = self.revision % 0xffffffff + 1
        return True

    def properties(self, item_id, requested=()):
        row = self.rows[item_id]
        return {key: value for key, value in row.items() if not requested or key in requested}

    def layout(self, parent=0, depth=-1, requested=()):
        row = self.properties(parent, requested)
        children = [self.layout(item_id, 0, requested) for item_id in self.rows if item_id] if parent == 0 and depth else []
        return parent, row, children

    def callback(self, item_id, event):
        if item_id not in self.rows:
            raise KeyError(item_id)
        return self.callbacks.get(item_id) if event == "clicked" else None


def _load_gi():
    # Importing the module and running its model tests does not require GTK/GI.
    from gi.repository import Gio, GLib
    return Gio, GLib


class StatusNotifier:
    """Own one tray item, recovering from watcher and session-bus restarts.

    Call start/stop/update on the GTK main context. Callback functions must
    return promptly; refresh callbacks should schedule the existing worker.
    ``registered`` is only true after an active tray host accepted registration.
    """

    def __init__(self, on_open, on_refresh, on_quit, *, icon_name=APP_ID,
                 icon_theme_path="", on_registration_changed=None, _gi=None):
        self.menu = MenuModel(on_open, on_refresh, on_quit)
        self.icon_name = icon_name
        self.icon_theme_path = str(icon_theme_path)
        self.on_registration_changed = on_registration_changed
        self._gi = _gi
        self._description = describe_snapshot(None)
        self.registered = False
        self.last_error = None
        self._started = False
        self._connection = None
        self._connect_pending = False
        self._connection_generation = 0
        self._closed_id = 0
        self._objects = []
        self._name_id = 0
        self._name_owned = False
        self._watches = []
        self._subscriptions = []
        self._watcher_owners = {}
        self._selected = None
        self._registration_pending = False
        self._registration_generation = 0
        self._retry_id = 0
        self._retry_count = 0
        self._bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"

    def start(self):
        if self._started:
            return True
        try:
            self.Gio, self.GLib = self._gi or _load_gi()
        except (ImportError, ValueError) as error:
            self.last_error = str(error)
            return False
        self._started = True
        self._connect()
        return True

    def stop(self):
        if not self._started:
            return
        self._started = False
        self._connection_generation += 1
        self._connect_pending = False
        self._cancel_retry()
        self._clear_connection(close=True)

    def update(self, snapshot):
        menu_changed = self.menu.update(snapshot)
        before = self._description
        self._description = describe_snapshot(snapshot)
        if not self._connection:
            return
        changed = {}
        for key, prop in (("label", "XAyatanaLabel"), ("tooltip", "ToolTip"), ("overlay", "OverlayIconName")):
            if before[key] != self._description[key]:
                changed[prop] = self._item_property(prop)
        if changed:
            self._emit(ITEM_PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                       self.GLib.Variant("(sa{sv}as)", (ITEM_INTERFACE, changed, [])))
        if "ToolTip" in changed:
            self._emit(ITEM_PATH, ITEM_INTERFACE, "NewToolTip", None)
        if "OverlayIconName" in changed:
            self._emit(ITEM_PATH, ITEM_INTERFACE, "NewOverlayIcon", None)
        if "XAyatanaLabel" in changed:
            self._emit(ITEM_PATH, ITEM_INTERFACE, "XAyatanaNewLabel",
                       self.GLib.Variant("(ss)", (self._description["label"], "Bluetooth 100%")))
        if menu_changed:
            self._emit(MENU_PATH, MENU_INTERFACE, "LayoutUpdated",
                       self.GLib.Variant("(ui)", (self.menu.revision, 0)))

    def _set_registered(self, value):
        value = bool(value)
        if self.registered != value:
            self.registered = value
            if self.on_registration_changed:
                self._queue_callback(lambda: self.on_registration_changed(value))

    def _queue_callback(self, callback):
        def run():
            try:
                callback()
            except Exception:
                LOG.exception("Tray action failed")
            return False
        self.GLib.idle_add(run)

    def _connect(self):
        if not self._started or self._connection or self._connect_pending:
            return
        self._connect_pending = True
        self._connection_generation += 1
        generation = self._connection_generation
        try:
            address = self.Gio.dbus_address_get_for_bus_sync(self.Gio.BusType.SESSION, None)
            flags = self.Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | self.Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
            self.Gio.DBusConnection.new_for_address(address, flags, None, None,
                                                   self._connected, generation)
        except Exception as error:
            self._connect_pending = False
            self.last_error = str(error)
            self._schedule_retry()

    def _connected(self, _source, result, generation):
        try:
            connection = self.Gio.DBusConnection.new_for_address_finish(result)
        except Exception as error:
            if generation == self._connection_generation:
                self._connect_pending = False
                self.last_error = str(error)
                self._schedule_retry()
            return
        if generation != self._connection_generation or not self._started:
            connection.close(None, None, None)
            return
        self._connect_pending = False
        self._connection = connection
        connection.set_exit_on_close(False)
        self._closed_id = connection.connect("closed", self._closed)
        try:
            for path, xml, method, getter in (
                (ITEM_PATH, ITEM_XML, self._item_method, self._get_item_property),
                (MENU_PATH, MENU_XML, self._menu_method, self._get_menu_property),
            ):
                info = self.Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
                self._objects.append(connection.register_object(path, info, method, getter, None))
            self._name_id = self.Gio.bus_own_name_on_connection(
                connection, self._bus_name, self.Gio.BusNameOwnerFlags.NONE,
                self._name_acquired, self._name_lost)
        except Exception as error:
            self.last_error = str(error)
            self._clear_connection(close=True)
            self._schedule_retry()

    def _name_acquired(self, connection, _name):
        if connection is not self._connection or not self._started:
            return
        self._name_owned = True
        for name in WATCHERS:
            self._watches.append(self.Gio.bus_watch_name_on_connection(
                connection, name, self.Gio.BusNameWatcherFlags.NONE,
                self._watcher_appeared, self._watcher_vanished))
            # Registering only when a tray host is present prevents the editor
            # hiding itself behind an icon that no desktop can display.
            self._subscriptions.append(connection.signal_subscribe(
                name, name, None, WATCHER_PATH, None, self.Gio.DBusSignalFlags.NONE,
                self._host_changed, name))

    def _name_lost(self, connection, _name):
        if connection is self._connection and self._started:
            self._name_owned = False
            self._set_registered(False)
            self.last_error = "The tray bus name is unavailable"

    def _watcher_appeared(self, connection, name, owner):
        if connection is self._connection and self._started:
            self._watcher_owners[name] = owner
            self._choose_watcher()

    def _watcher_vanished(self, connection, name):
        if connection is self._connection:
            self._watcher_owners.pop(name, None)
            self._choose_watcher()

    def _choose_watcher(self):
        # A host may own both aliases. Register exactly once, preferring the
        # deployed KDE interface, to avoid duplicate GNOME menu items.
        selected = next(((name, self._watcher_owners[name]) for name in WATCHERS
                         if name in self._watcher_owners), None)
        if selected != self._selected:
            self._selected = selected
            self._registration_generation += 1
            self._registration_pending = False
            self._cancel_retry()
            self._set_registered(False)
            self._retry_count = 0
        if selected:
            self._register()

    def _host_changed(self, connection, _sender, _path, _iface, signal, _parameters, name):
        if connection is not self._connection or not self._selected or name != self._selected[0]:
            return
        if signal in ("StatusNotifierHostRegistered", "StatusNotifierHostUnregistered"):
            self._registration_generation += 1
            self._registration_pending = False
            self._set_registered(False)
            self._register()

    def _register(self):
        if (not self._started or not self._connection or not self._selected or
                not self._name_owned or self.registered or self._registration_pending):
            return
        self._registration_pending = True
        context = (self._connection, self._selected, self._registration_generation)
        self._connection.call(
            self._selected[0], WATCHER_PATH, "org.freedesktop.DBus.Properties", "Get",
            self.GLib.Variant("(ss)", (self._selected[0], "IsStatusNotifierHostRegistered")),
            self.GLib.VariantType.new("(v)"), self.Gio.DBusCallFlags.NO_AUTO_START,
            3000, None, self._host_checked, context)

    def _current(self, context):
        connection, selected, generation = context
        return (self._started and connection is self._connection and selected == self._selected
                and generation == self._registration_generation)

    def _host_checked(self, connection, result, context):
        try:
            value = connection.call_finish(result).unpack()[0]
            if hasattr(value, "unpack"):
                value = value.unpack()
        except Exception as error:
            if self._current(context):
                self._registration_pending = False
                self.last_error = str(error)
                self._schedule_retry()
            return
        if not self._current(context):
            return
        if not value:
            self._registration_pending = False
            self.last_error = "No active desktop tray host"
            return
        connection.call(context[1][0], WATCHER_PATH, context[1][0], "RegisterStatusNotifierItem",
                        self.GLib.Variant("(s)", (self._bus_name,)), None,
                        self.Gio.DBusCallFlags.NO_AUTO_START, 3000, None,
                        self._registered, context)

    def _registered(self, connection, result, context):
        try:
            connection.call_finish(result)
        except Exception as error:
            if self._current(context):
                self._registration_pending = False
                self.last_error = str(error)
                self._schedule_retry()
            return
        if self._current(context):
            self._registration_pending = False
            self._retry_count = 0
            self.last_error = None
            self._cancel_retry()
            self._set_registered(True)

    def _cancel_retry(self):
        if self._retry_id:
            self.GLib.source_remove(self._retry_id)
            self._retry_id = 0

    def _schedule_retry(self):
        if not self._started or self._retry_id:
            return
        delay = (3, 10, 30, 60)[min(self._retry_count, 3)]
        self._retry_count += 1
        def retry():
            self._retry_id = 0
            if self._connection:
                self._register()
            else:
                self._connect()
            return False
        self._retry_id = self.GLib.timeout_add_seconds(delay, retry)

    def _closed(self, connection, _remote_peer_vanished, error):
        if connection is self._connection:
            self.last_error = str(error or "Session bus disconnected")
            self._clear_connection(close=False)
            self._schedule_retry()

    def _clear_connection(self, *, close):
        connection = self._connection
        self._connection = None
        self._registration_generation += 1
        self._registration_pending = False
        self._selected = None
        self._watcher_owners.clear()
        self._set_registered(False)
        if connection:
            if self._closed_id:
                connection.disconnect(self._closed_id)
            for watch in self._watches:
                self.Gio.bus_unwatch_name(watch)
            for subscription in self._subscriptions:
                connection.signal_unsubscribe(subscription)
            for registration in self._objects:
                connection.unregister_object(registration)
            if self._name_id:
                self.Gio.bus_unown_name(self._name_id)
            if close and not connection.is_closed():
                connection.close(None, None, None)
        self._closed_id = self._name_id = 0
        self._name_owned = False
        self._watches.clear()
        self._subscriptions.clear()
        self._objects.clear()

    def _emit(self, path, interface, signal, parameters):
        if self._connection and not self._connection.is_closed():
            try:
                self._connection.emit_signal(None, path, interface, signal, parameters)
            except Exception as error:
                self.last_error = str(error)

    def _item_property(self, name):
        values = {
            "Category": ("s", "Hardware"), "Id": ("s", APP_ID), "Title": ("s", APP_NAME),
            "Status": ("s", "Active"), "WindowId": ("i", 0),
            "IconThemePath": ("s", self.icon_theme_path), "IconName": ("s", self.icon_name),
            "IconPixmap": ("a(iiay)", []), "OverlayIconName": ("s", self._description["overlay"]),
            "OverlayIconPixmap": ("a(iiay)", []), "AttentionIconName": ("s", ""),
            "AttentionIconPixmap": ("a(iiay)", []), "AttentionMovieName": ("s", ""),
            "ToolTip": ("(sa(iiay)ss)", (self.icon_name, [], APP_NAME, self._description["tooltip"])),
            "ItemIsMenu": ("b", True), "Menu": ("o", MENU_PATH),
            "XAyatanaLabel": ("s", self._description["label"]),
            "XAyatanaLabelGuide": ("s", "Bluetooth 100%"),
        }
        signature, value = values[name]
        return self.GLib.Variant(signature, value)

    def _get_item_property(self, _connection, _sender, _path, _interface, name):
        return self._item_property(name)

    def _get_menu_property(self, _connection, _sender, _path, _interface, name):
        values = {"Version": ("u", 3), "TextDirection": ("s", "ltr"),
                  "Status": ("s", "normal"),
                  "IconThemePath": ("as", [self.icon_theme_path] if self.icon_theme_path else [])}
        signature, value = values[name]
        return self.GLib.Variant(signature, value)

    def _item_method(self, _connection, _sender, _path, _interface, method, _parameters, invocation):
        invocation.return_value(None)
        if method in ("Activate", "SecondaryActivate", "XAyatanaSecondaryActivate"):
            self._queue_callback(self.menu.callbacks[MenuModel.OPEN])

    def _variant_properties(self, properties):
        return {key: self.GLib.Variant("b" if type(value) is bool else "s", value)
                for key, value in properties.items()}

    def _variant_layout(self, item_id, depth, requested):
        row_id, properties, children = self.menu.layout(item_id, depth, requested)
        return self.GLib.Variant("(ia{sv}av)", (row_id, self._variant_properties(properties), [
            self.GLib.Variant("(ia{sv}av)", (child_id, self._variant_properties(child_props), []))
            for child_id, child_props, _ in children]))

    def _event(self, item_id, event):
        callback = self.menu.callback(item_id, event)
        if callback:
            self._queue_callback(callback)

    def _menu_method(self, _connection, _sender, _path, _interface, method, parameters, invocation):
        args = parameters.unpack()
        try:
            result = None
            if method == "GetLayout":
                parent, depth, requested = args
                layout = self._variant_layout(parent, depth, requested)
                # unpack() recursively removes the a{sv} variants; retain the
                # constructed layout when wrapping the method's output tuple.
                result = self.GLib.Variant.new_tuple(self.GLib.Variant("u", self.menu.revision), layout)
            elif method == "GetGroupProperties":
                ids, requested = args
                rows = [(item_id, self._variant_properties(self.menu.properties(item_id, requested)))
                        for item_id in ids if item_id in self.menu.rows]
                result = self.GLib.Variant("(a(ia{sv}))", (rows,))
            elif method == "GetProperty":
                item_id, name = args
                properties = self._variant_properties(self.menu.properties(item_id))
                result = self.GLib.Variant("(v)", (properties[name],))
            elif method == "Event":
                self._event(args[0], args[1])
            elif method == "EventGroup":
                errors = []
                for item_id, event, _data, _timestamp in args[0]:
                    try:
                        self._event(item_id, event)
                    except KeyError:
                        errors.append(item_id)
                result = self.GLib.Variant("(ai)", (errors,))
            elif method == "AboutToShow":
                if args[0] not in self.menu.rows:
                    raise KeyError(args[0])
                result = self.GLib.Variant("(b)", (False,))
            elif method == "AboutToShowGroup":
                result = self.GLib.Variant("(aiai)", ([], [i for i in args[0] if i not in self.menu.rows]))
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
                return
            invocation.return_value(result)
        except (KeyError, ValueError, TypeError):
            invocation.return_dbus_error("com.canonical.dbusmenu.Error.InvalidMenuItem", "Unknown menu item or property")
