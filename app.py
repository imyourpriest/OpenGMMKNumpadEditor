#!/usr/bin/env python3
"""Native profile editor for the stock GMMK Numpad.

Device communication is separated from this editor. Local drafts must never
be represented as settings read from, or saved to, the connected keyboard.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import subprocess
import threading
import uuid

import gi
from gi.repository import GLib

APP_ID = "io.github.opengmmk.NumpadEditor"
APP_NAME = "OpenGMMK Numpad Editor"
APP_VERSION = "0.1.0"
PROJECT_URL = "https://github.com/imyourpriest/OpenGMMKNumpadEditor"
# Establish the process identity before importing GTK, which initializes GDK.
# GNOME matches Wayland windows by the application ID and X11 windows by WM_CLASS.
GLib.set_prgname(APP_ID)
GLib.set_application_name(APP_NAME)

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk
from gi.repository import Gio, Gtk
# GTK's initial GDK setup capitalizes the program class. Set the exact desktop
# entry class afterwards, before creating any window, so X11 matching is exact.
Gdk.set_program_class(APP_ID)

from device import scan_devices, select_device
from profile import PHYSICAL_KEYS, RGB_EFFECTS, load_document, new_document, save_document
from protocol import capabilities


KEY_NAMES = {0: "Disabled"}
KEY_NAMES.update({4 + i: chr(65 + i) for i in range(26)})
KEY_NAMES.update({30 + i: str(i + 1) for i in range(9)})
KEY_NAMES.update({39: "0", 40: "Enter", 41: "Escape", 42: "Backspace",
                  43: "Tab", 44: "Space", 45: "-", 46: "=", 47: "[", 48: "]",
                  49: "Backslash", 51: ";", 52: "Apostrophe", 53: "Grave",
                  54: ",", 55: ".", 56: "/", 57: "Caps Lock"})
KEY_NAMES.update({58 + i: f"F{i + 1}" for i in range(12)})
KEY_NAMES.update({70: "Print Screen", 71: "Scroll Lock", 72: "Pause",
                  73: "Insert", 74: "Home", 75: "Page Up", 76: "Delete",
                  77: "End", 78: "Page Down", 79: "Right", 80: "Left",
                  81: "Down", 82: "Up", 83: "Num Lock", 84: "Numpad /",
                  85: "Numpad *", 86: "Numpad -", 87: "Numpad +",
                  88: "Numpad Enter", 98: "Numpad 0", 99: "Numpad ."})
KEY_NAMES.update({89 + i: f"Numpad {i + 1}" for i in range(9)})


class Editor(Gtk.ApplicationWindow):
    def __init__(self, application):
        super().__init__(application=application, title=APP_NAME)
        self.set_icon_name(APP_ID)
        titlebar = Gtk.HeaderBar(title=APP_NAME, show_close_button=True)
        titlebar.pack_start(Gtk.Image.new_from_icon_name(APP_ID, Gtk.IconSize.SMALL_TOOLBAR))
        about = Gtk.Button.new_from_icon_name("help-about-symbolic", Gtk.IconSize.BUTTON)
        about.set_tooltip_text(f"About {APP_NAME}")
        about.get_accessible().set_name(f"About {APP_NAME}")
        about.connect("clicked", self.show_about)
        titlebar.pack_end(about)
        self.set_titlebar(titlebar)
        self.set_default_size(940, 820)
        self.set_border_width(22)
        self.document = new_document()
        self.path = None
        self.dirty = False
        self.loading = False
        self.selected_key = PHYSICAL_KEYS[0]["id"]
        self.devices = []
        self.busy = False
        self.key_buttons = {}
        self.key_css = {}
        self.connect("delete-event", self.on_close)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.add(root)
        heading = Gtk.Label(xalign=0)
        heading.set_markup(f'<span size="xx-large" weight="bold">{APP_NAME}</span>')
        root.pack_start(heading, False, False, 0)
        self.device_label = Gtk.Label(xalign=0)
        root.pack_start(self.device_label, False, False, 0)

        notice = Gtk.InfoBar()
        notice.set_message_type(Gtk.MessageType.INFO)
        message = Gtk.Label(xalign=0)
        message.set_line_wrap(True)
        message.set_text("Edit locally, then choose what to send over USB. Existing onboard settings cannot be read back. "
                         "Device writes are experimental until tested on your numpad.")
        notice.get_content_area().add(message)
        root.pack_start(notice, False, False, 0)

        profiles = Gtk.Box(spacing=8)
        profiles.pack_start(Gtk.Label(label="Local profile", xalign=0), False, False, 0)
        self.profile_combo = Gtk.ComboBoxText()
        self.profile_combo.connect("changed", self.profile_changed)
        profiles.pack_start(self.profile_combo, True, True, 0)
        for title, action in (("New", self.add_profile), ("Duplicate", self.duplicate_profile),
                              ("Rename", self.rename_profile), ("Delete", self.delete_profile)):
            button = Gtk.Button(label=title)
            button.connect("clicked", action)
            profiles.pack_start(button, False, False, 0)
        root.pack_start(profiles, False, False, 0)

        content = Gtk.Box(spacing=26)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_overlay_scrolling(False)
        scroll.set_min_content_height(220)
        scroll.add(content)
        root.pack_start(scroll, True, True, 0)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.pack_start(left, False, False, 0)
        left.pack_start(Gtk.Label(label="Select a key to edit", xalign=0), False, False, 0)
        grid = Gtk.Grid(column_spacing=7, row_spacing=7)
        left.pack_start(grid, False, False, 0)
        for item in PHYSICAL_KEYS:
            button = Gtk.Button()
            button.set_size_request(74 * item.get("width", 1), 62 * item.get("height", 1))
            button.connect("clicked", self.key_selected, item["id"])
            grid.attach(button, item["col"], item["row"], item.get("width", 1), item.get("height", 1))
            provider = Gtk.CssProvider()
            button.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            self.key_buttons[item["id"]] = button
            self.key_css[item["id"]] = provider
        preview = Gtk.Label(label="Lighting preview. Changes reach the numpad only when you save lighting.", xalign=0)
        preview.set_line_wrap(True)
        preview.set_max_width_chars(35)
        left.pack_start(preview, False, False, 0)
        details = Gtk.Expander(label="Connected device details")
        self.details_label = Gtk.Label(xalign=0, selectable=True)
        self.details_label.set_line_wrap(True)
        self.details_label.set_max_width_chars(40)
        details.add(self.details_label)
        left.pack_start(details, False, False, 0)
        refresh = Gtk.Button(label="Refresh device")
        refresh.connect("clicked", lambda *_: self.refresh_device())
        left.pack_start(refresh, False, False, 0)
        self.read_button = Gtk.Button(label="Read battery / firmware")
        self.read_button.connect("clicked", self.read_device_status)
        left.pack_start(self.read_button, False, False, 0)
        self.access_button = Gtk.Button(label="Set up USB access…")
        self.access_button.connect("clicked", self.setup_access)
        left.pack_start(self.access_button, False, False, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.pack_start(right, True, True, 0)
        self.key_heading = Gtk.Label(xalign=0)
        right.pack_start(self.key_heading, False, False, 0)
        self.binding = Gtk.ComboBoxText()
        for usage, name in sorted(KEY_NAMES.items(), key=lambda item: (item[1] != "Print Screen", item[1])):
            self.binding.append(str(usage), name)
        self.binding.connect("changed", self.binding_changed)
        right.pack_start(self.binding, False, False, 0)
        modifiers = Gtk.Box(spacing=8)
        self.modifiers = {}
        for value, name in (("ctrl", "Ctrl"), ("alt", "Alt"), ("shift", "Shift"), ("super", "Super")):
            check = Gtk.CheckButton(label=name)
            check.connect("toggled", self.binding_changed)
            self.modifiers[value] = check
            modifiers.pack_start(check, False, False, 0)
        right.pack_start(modifiers, False, False, 0)
        restore = Gtk.Button(label="Restore this key’s standard assignment")
        restore.connect("clicked", self.restore_key)
        right.pack_start(restore, False, False, 0)
        right.pack_start(Gtk.Separator(), False, False, 6)
        rgb_heading = Gtk.Label(xalign=0)
        rgb_heading.set_markup('<b>Lighting</b>')
        right.pack_start(rgb_heading, False, False, 0)
        self.effect = Gtk.ComboBoxText()
        for name in RGB_EFFECTS:
            self.effect.append(name, name.replace("_", " ").title())
        self.effect.connect("changed", self.rgb_changed)
        right.pack_start(self.effect, False, False, 0)
        color_row = Gtk.Box(spacing=8)
        color_row.pack_start(Gtk.Label(label="Base color", xalign=0), True, True, 0)
        self.color = Gtk.ColorButton()
        self.color.connect("color-set", self.rgb_changed)
        color_row.pack_start(self.color, False, False, 0)
        right.pack_start(color_row, False, False, 0)
        self.brightness = self.make_scale(right, "Brightness", self.rgb_changed)
        self.speed = self.make_scale(right, "Effect speed", self.rgb_changed)
        key_color_row = Gtk.Box(spacing=8)
        key_color_row.pack_start(Gtk.Label(label="Selected key color", xalign=0), True, True, 0)
        self.key_color = Gtk.ColorButton()
        self.key_color.connect("color-set", self.key_color_changed)
        key_color_row.pack_start(self.key_color, False, False, 0)
        clear_color = Gtk.Button(label="Use base color")
        clear_color.connect("clicked", self.clear_key_color)
        key_color_row.pack_start(clear_color, False, False, 0)
        right.pack_start(key_color_row, False, False, 0)
        controls_note = Gtk.Label(label="Stock firmware is retained. Knob volume turning and the Ubuntu slider helper remain available.", xalign=0)
        controls_note.set_line_wrap(True)
        controls_note.set_max_width_chars(42)
        right.pack_start(controls_note, False, False, 0)

        footer = Gtk.Box(spacing=8)
        for title, action in (("Open profile file…", self.open_file), ("Save profile file…", self.save_file)):
            button = Gtk.Button(label=title)
            button.connect("clicked", action)
            footer.pack_start(button, False, False, 0)
        target = Gtk.Box(spacing=10)
        target.pack_start(Gtk.Label(label="Onboard destination"), False, False, 0)
        self.slot = Gtk.ComboBoxText()
        self.layer = Gtk.ComboBoxText()
        for number in range(1, 4):
            self.slot.append(str(number), f"Profile {number}")
            self.layer.append(str(number), f"Layer {number}")
        self.slot.set_active(0); self.layer.set_active(0)
        target.pack_start(self.slot, False, False, 0)
        target.pack_start(self.layer, False, False, 0)
        self.select_button = Gtk.Button(label="Activate")
        self.select_button.connect("clicked", self.activate_slot)
        target.pack_end(self.select_button, False, False, 0)
        root.pack_start(target, False, False, 0)
        self.device_save = Gtk.Button(label="Save keybindings to numpad…")
        self.device_save.set_sensitive(False)
        self.device_save.connect("clicked", self.save_bindings)
        footer.pack_end(self.device_save, False, False, 0)
        self.lighting_save = Gtk.Button(label="Save lighting…")
        self.lighting_save.connect("clicked", self.save_lighting)
        footer.pack_end(self.lighting_save, False, False, 0)
        root.pack_start(footer, False, False, 0)
        self.status = Gtk.Label(xalign=0, ellipsize=3)
        root.pack_start(self.status, False, False, 0)
        self.refresh_profiles()
        self.refresh_device()
        self.show_all()

    def current_profile(self):
        selected = self.document["selected_profile_id"]
        return next(p for p in self.document["profiles"] if p["id"] == selected)

    def changed(self):
        if self.loading:
            return
        self.dirty = True
        self.status.set_text("Unsaved local changes · Current edits are still local")
        self.update_preview()

    def refresh_profiles(self):
        self.loading = True
        self.profile_combo.remove_all()
        for item in self.document["profiles"]:
            self.profile_combo.append(item["id"], item["name"])
        self.profile_combo.set_active_id(self.document["selected_profile_id"])
        self.loading = False
        self.load_controls()
        self.status.set_text("New local draft · Not read from the connected device" if self.path is None else f"Local file: {self.path}")

    def profile_changed(self, combo):
        if self.loading or combo.get_active_id() is None:
            return
        self.document["selected_profile_id"] = combo.get_active_id()
        self.load_controls()

    def load_controls(self):
        self.loading = True
        profile = self.current_profile()
        action = profile["bindings"][self.selected_key]
        usage = str(action["usage_id"])
        if action["usage_id"] not in KEY_NAMES:
            self.binding.append(usage, f"Keyboard usage {action['usage_id']}")
        self.binding.set_active_id(usage)
        for name, widget in self.modifiers.items():
            widget.set_active(name in action.get("modifiers", []))
        label = next(k["label"] for k in PHYSICAL_KEYS if k["id"] == self.selected_key)
        self.key_heading.set_markup(f"<b>Key: {GLib.markup_escape_text(label)}</b>")
        rgb = profile["rgb"]
        self.effect.set_active_id(rgb["effect"])
        self.set_color(self.color, rgb["color"])
        self.set_color(self.key_color, rgb["per_key"].get(self.selected_key, rgb["color"]))
        self.brightness.set_value(rgb["brightness"])
        self.speed.set_value(rgb["speed"])
        self.loading = False
        self.update_preview()

    def key_selected(self, _button, key):
        self.selected_key = key
        self.load_controls()

    def binding_changed(self, *_):
        if self.loading or self.binding.get_active_id() is None:
            return
        self.current_profile()["bindings"][self.selected_key] = {
            "usage_page": 7, "usage_id": int(self.binding.get_active_id()),
            "modifiers": [name for name, check in self.modifiers.items() if check.get_active()],
        }
        self.changed()

    def restore_key(self, *_):
        key = next(item for item in PHYSICAL_KEYS if item["id"] == self.selected_key)
        self.current_profile()["bindings"][self.selected_key] = {"usage_page": 7, "usage_id": key["default_usage"], "modifiers": []}
        self.load_controls()
        self.changed()

    def rgb_changed(self, *_):
        if self.loading or self.effect.get_active_id() is None:
            return
        rgb = self.current_profile()["rgb"]
        rgb.update(effect=self.effect.get_active_id(), color=self.get_color(self.color),
                   brightness=round(self.brightness.get_value()), speed=round(self.speed.get_value()))
        self.changed()

    def key_color_changed(self, *_):
        if not self.loading:
            self.current_profile()["rgb"]["per_key"][self.selected_key] = self.get_color(self.key_color)
            self.changed()

    def clear_key_color(self, *_):
        self.current_profile()["rgb"]["per_key"].pop(self.selected_key, None)
        self.load_controls()
        self.changed()

    def update_preview(self):
        profile = self.current_profile()
        rgb = profile["rgb"]
        for item in PHYSICAL_KEYS:
            key = item["id"]
            action = profile["bindings"][key]
            value = KEY_NAMES.get(action["usage_id"], str(action["usage_id"]))
            modifiers = "+".join(action.get("modifiers", []))
            value = f"{modifiers}+{value}" if modifiers else value
            self.key_buttons[key].set_label(f"{item['label']}\n{value}")
            color = rgb["per_key"].get(key, rgb["color"])
            brightness = rgb["brightness"] / 100 if rgb["effect"] != "off" else 0
            channels = [round(int(color[index:index+2], 16) * brightness) for index in (1, 3, 5)]
            border = "#ffffff" if key == self.selected_key else "#697586"
            text_color = "#111111" if sum(c * w for c, w in zip(channels, (0.2126, 0.7152, 0.0722))) > 160 else "white"
            css = f"button {{background-image:none; background-color:rgb({channels[0]},{channels[1]},{channels[2]}); color:{text_color}; border:2px solid {border}; border-radius:8px; padding:5px; text-shadow:none;}}"
            self.key_css[key].load_from_data(css.encode())

    def refresh_device(self):
        try:
            self.devices = scan_devices()
            device = select_device(self.devices)
            if device:
                self.device_label.set_text("Glorious GMMK Numpad detected · Stock firmware retained")
                self.details_label.set_text(json.dumps(device, indent=2, sort_keys=True))
            elif len([d for d in self.devices if d.get("supported_identity")]) > 1:
                self.device_label.set_text("Multiple matching numpads detected · Choose one connection")
                self.details_label.set_text("Local editing is available. Device saving is unavailable.")
            else:
                self.device_label.set_text("No supported numpad detected · Local editing is available")
                self.details_label.set_text(json.dumps(self.devices, indent=2))
            status = capabilities(device)
            available = bool(status.get("can_write_device", False)) and not self.busy
            for widget in (self.device_save, self.lighting_save, self.select_button, self.read_button):
                widget.set_sensitive(available)
            self.device_save.set_tooltip_text(status.get("reason", "Stock protocol not verified"))
            self.access_button.set_sensitive(device is not None and not available and not self.busy)
            if device and not available and not self.busy:
                self.device_label.set_text("GMMK Numpad detected · USB access setup may be needed")
        except (OSError, ValueError) as exc:
            self.device_label.set_text("Device detection unavailable · Local editing is available")
            self.details_label.set_text(str(exc))
            for widget in (self.device_save, self.lighting_save, self.select_button, self.read_button):
                widget.set_sensitive(False)
            self.access_button.set_sensitive(False)

    def setup_access(self, *_):
        try:
            subprocess.Popen(["gnome-terminal", f"--title={APP_NAME} — USB access setup", "--", "/bin/sh",
                              str(Path(__file__).resolve().parent / 'install-device-access.sh')],
                             close_fds=True)
            self.status.set_text("Complete the Ubuntu password prompt in the terminal, then Refresh device.")
        except OSError as exc:
            self.error(f"Could not open Ubuntu Terminal: {exc}. Run install-device-access.sh in a normal terminal.")

    def destination(self):
        return int(self.slot.get_active_id()), int(self.layer.get_active_id())

    def confirm(self, heading, explanation, action):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True,
                                   message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.NONE, text=heading)
        dialog.format_secondary_text(explanation)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, action, Gtk.ResponseType.OK)
        result = dialog.run() == Gtk.ResponseType.OK
        dialog.destroy()
        return result

    def run_device_task(self, function, done):
        if self.busy:
            return
        self.busy = True
        self.refresh_device()
        self.status.set_text("Communicating with the numpad…")
        def finish(result, error):
            self.busy = False
            self.refresh_device()
            if error:
                self.status.set_text("Device operation did not finish · Check the numpad before retrying")
                self.error(str(error))
            else:
                self.status.set_text(done(result) if callable(done) else done)
            return False
        def work():
            try:
                result, error = function(), None
            except Exception as exc:
                result, error = None, exc
            GLib.idle_add(finish, result, error)
        threading.Thread(target=work, daemon=True).start()

    def read_device_status(self, *_):
        import backend
        self.run_device_task(backend.read_status, lambda result: "Device status: " + json.dumps(result, ensure_ascii=False))

    def activate_slot(self, *_):
        import backend
        slot, layer = self.destination()
        self.run_device_task(lambda: backend.select_profile(slot, layer),
                             f"Activation command sent: Profile {slot}, Layer {layer}. Check the numpad.")

    def save_bindings(self, *_):
        import backend
        slot, layer = self.destination()
        profile = copy.deepcopy(self.current_profile())
        explanation = (f"Replace all 17 key assignments in Profile {slot}, Layer {layer} with “{profile['name']}”? "
                       "Knob-press is restored to Calculator. Existing assignments cannot be read or backed up. "
                       "This also activates the destination. Knob volume turning, slider configuration, and RGB are not written. "
                       "After saving, test the keys and reconnect USB to check persistence.")
        if self.confirm("Replace this onboard key layer?", explanation, "Save keybindings"):
            self.run_device_task(lambda: backend.write_bindings(profile, slot, layer),
                                 "Keybinding commands sent. Test the keys, then reconnect USB to check persistence.")

    def save_lighting(self, *_):
        import backend
        slot, layer = self.destination()
        profile = copy.deepcopy(self.current_profile())
        dialog = Gtk.Dialog(title="Save lighting and device settings", transient_for=self, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save lighting", Gtk.ResponseType.OK)
        box = dialog.get_content_area(); box.set_spacing(10); box.set_border_width(18)
        explanation = Gtk.Label(xalign=0, label=(f"Write lighting to Profile {slot}, Layer {layer}, then activate it.\n"
            "CORE combines brightness with the settings below.\n"
            "These choices are saved with your local profile; they are not read from the numpad.\n"
            "The command also sets debounce to 8, Windows lock off, and per-key lighting on."))
        explanation.set_line_wrap(True); box.pack_start(explanation, False, False, 0)
        previous = profile["rgb"]["device_settings"]
        numlock = Gtk.CheckButton(label="Num Lock indicator lighting"); numlock.set_active(previous['num_lock_indicator'])
        battery = Gtk.CheckButton(label="Battery indicator lighting"); battery.set_active(previous['battery_indicator'])
        box.pack_start(numlock, False, False, 0); box.pack_start(battery, False, False, 0)
        sensitivity = self.make_scale(box, "Volume sensitivity (CORE setting)", lambda *_: None)
        sensitivity.set_value(previous['sensitivity'])
        wireless = self.make_scale(box, "Bluetooth lighting brightness", lambda *_: None)
        wireless.set_value(previous['wireless_brightness'])
        note = Gtk.Label(xalign=0, label="The custom color layer is replaced; unassigned keys and sidelights follow the global effect.\n"
                                       "Existing lighting/settings cannot be read back first.")
        note.set_line_wrap(True); box.pack_start(note, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        settings = dict(num_lock_indicator=numlock.get_active(), battery_indicator=battery.get_active(),
                        sensitivity=round(sensitivity.get_value()), wireless_brightness=round(wireless.get_value()))
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            profile['rgb']['device_settings'] = dict(settings)
            self.current_profile()['rgb']['device_settings'] = dict(settings)
            self.changed()
            self.run_device_task(lambda: backend.write_lighting(profile, slot, layer, settings),
                                 "Lighting commands sent. Check the RGB, slider, and USB reconnect persistence.")

    def request_name(self, title, initial):
        dialog = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.OK)
        entry = Gtk.Entry(text=initial)
        entry.set_max_length(80)
        entry.set_margin_start(18); entry.set_margin_end(18)
        entry.set_margin_top(18); entry.set_margin_bottom(18)
        entry.set_activates_default(True)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.get_content_area().add(entry)
        dialog.show_all()
        answer = dialog.run()
        text = entry.get_text().strip()
        dialog.destroy()
        return text if answer == Gtk.ResponseType.OK and text else None

    def add_profile(self, *_):
        if len(self.document["profiles"]) >= 32:
            self.error("A file can contain up to 32 local profiles.")
            return
        name = self.request_name("New local profile", "New profile")
        if name:
            item = new_document()["profiles"][0]
            item.update(id=uuid.uuid4().hex, name=name)
            self.document["profiles"].append(item)
            self.document["selected_profile_id"] = item["id"]
            self.refresh_profiles(); self.changed()

    def duplicate_profile(self, *_):
        if len(self.document["profiles"]) >= 32:
            self.error("A file can contain up to 32 local profiles.")
            return
        item = copy.deepcopy(self.current_profile())
        item.update(id=uuid.uuid4().hex, name=item["name"][:75] + " copy")
        self.document["profiles"].append(item)
        self.document["selected_profile_id"] = item["id"]
        self.refresh_profiles(); self.changed()

    def rename_profile(self, *_):
        name = self.request_name("Rename local profile", self.current_profile()["name"])
        if name:
            self.current_profile()["name"] = name
            self.refresh_profiles(); self.changed()

    def delete_profile(self, *_):
        if len(self.document["profiles"]) == 1:
            self.error("Keep at least one local profile.")
            return
        item = self.current_profile()
        self.document["profiles"].remove(item)
        self.document["selected_profile_id"] = self.document["profiles"][0]["id"]
        self.refresh_profiles(); self.changed()

    def discard_ok(self):
        if not self.dirty:
            return True
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.NONE, text="Discard unsaved local changes?")
        dialog.add_buttons("Keep editing", Gtk.ResponseType.CANCEL, "Discard", Gtk.ResponseType.OK)
        result = dialog.run() == Gtk.ResponseType.OK
        dialog.destroy()
        return result

    def on_close(self, *_):
        if self.busy:
            self.error("Wait for the current device operation to finish before closing the editor.")
            return True
        return not self.discard_ok()

    def open_file(self, *_):
        if not self.discard_ok():
            return
        chooser = Gtk.FileChooserDialog(title="Open a local profile file", transient_for=self,
                                         action=Gtk.FileChooserAction.OPEN)
        chooser.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Open", Gtk.ResponseType.OK)
        if chooser.run() == Gtk.ResponseType.OK:
            try:
                path = Path(chooser.get_filename())
                document = load_document(path)
                self.document, self.path, self.dirty = document, path, False
                self.refresh_profiles()
            except (OSError, ValueError) as exc:
                self.error(str(exc))
        chooser.destroy()

    def save_file(self, *_):
        chooser = Gtk.FileChooserDialog(title="Save profiles on this computer", transient_for=self,
                                         action=Gtk.FileChooserAction.SAVE)
        chooser.set_do_overwrite_confirmation(True)
        chooser.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.OK)
        chooser.set_current_name(self.path.name if self.path else "gmmk-profiles.json")
        try:
            folder = self.path.parent if self.path else Path.home() / 'Documents' / 'GMMK Numpad Profiles'
            folder.mkdir(parents=True, exist_ok=True)
            chooser.set_current_folder(str(folder))
        except OSError:
            chooser.set_current_folder(str(Path.home()))
        if chooser.run() == Gtk.ResponseType.OK:
            path = Path(chooser.get_filename())
            try:
                save_document(path, self.document)
                self.path, self.dirty = path, False
                self.status.set_text(f"Saved locally: {path} · Not written to the numpad")
            except (OSError, ValueError) as exc:
                self.error(str(exc))
        chooser.destroy()

    def error(self, text):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.ERROR,
                                   buttons=Gtk.ButtonsType.CLOSE, text="Could not complete that action")
        dialog.format_secondary_text(text)
        dialog.run(); dialog.destroy()

    def show_about(self, _button=None):
        dialog = Gtk.AboutDialog(
            transient_for=self, modal=True, program_name=APP_NAME, version=APP_VERSION,
            logo_icon_name=APP_ID, website=PROJECT_URL, website_label="Project on GitHub",
            comments="Keybindings, onboard profiles, and RGB for the stock Glorious GMMK Numpad on Linux.",
            license_type=Gtk.License.MIT_X11,
            copyright="Copyright © 2026 OpenGMMK Numpad Editor contributors",
        )
        dialog.run()
        dialog.destroy()

    @staticmethod
    def make_scale(parent, label, callback):
        parent.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        scale.set_digits(0)
        scale.connect("value-changed", callback)
        parent.pack_start(scale, False, False, 0)
        return scale

    @staticmethod
    def set_color(widget, value):
        color = Gdk.RGBA(); color.parse(value); widget.set_rgba(color)

    @staticmethod
    def get_color(widget):
        color = widget.get_rgba()
        return "#" + "".join(f"{round(channel * 255):02x}" for channel in (color.red, color.green, color.blue))


class Application(Gtk.Application):
    def __init__(self, smoke_test=False):
        flags = Gio.ApplicationFlags.NON_UNIQUE if smoke_test else Gio.ApplicationFlags.DEFAULT_FLAGS
        super().__init__(application_id=APP_ID, flags=flags)
        self.smoke_test = smoke_test

    def do_startup(self):
        Gtk.Application.do_startup(self)
        # Also find the bundled icon when the portable folder is run directly.
        Gtk.IconTheme.get_default().append_search_path(str(Path(__file__).resolve().parent / "assets"))
        Gtk.Window.set_default_icon_name(APP_ID)

    def do_activate(self):
        window = self.get_active_window()
        if window is None:
            window = Editor(self)
        window.present()
        if self.smoke_test:
            GLib.timeout_add(250, self.quit)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    parser.add_argument("--smoke-test", action="store_true", help="construct the UI and close it without device writes")
    args = parser.parse_args()
    raise SystemExit(Application(args.smoke_test).run([]))
