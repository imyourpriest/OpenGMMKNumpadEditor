"""Application lifecycle behavior without a display, GI, USB, or desktop bus.

The real callback methods run against small window/loop doubles. GTK widget
construction stays in the separate live smoke check; these tests cover data
loss prevention and background-worker recovery on ordinary headless CI.
"""
import importlib.util
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from monitoring import MonitorSnapshot
from status_notifier import StatusNotifier


def load_application():
    gi = ModuleType("gi")
    gi.require_version = Mock()
    repository = ModuleType("gi.repository")
    glib = SimpleNamespace(set_prgname=Mock(), set_application_name=Mock(),
                           idle_add=Mock(), source_remove=Mock())
    class FakeGtkApplication:
        do_shutdown = Mock()
    repository.GLib = glib
    repository.Gio = SimpleNamespace()
    repository.Gdk = SimpleNamespace(set_program_class=Mock())
    repository.Gtk = SimpleNamespace(Application=FakeGtkApplication, ApplicationWindow=object)
    path = Path(__file__).resolve().parents[1] / "app.py"
    spec = importlib.util.spec_from_file_location("opengmmk_headless_application", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}):
        spec.loader.exec_module(module)
    return module


class ApplicationLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = load_application()

    def window(self, *, busy=False, dirty=False):
        return SimpleNamespace(busy=busy, dirty=dirty, hide=Mock(), error=Mock(),
            discard_ok=Mock(return_value=True), update_tray_status=Mock(),
            update_monitor=Mock(), refresh_device=Mock())

    def application(self, window=None):
        return SimpleNamespace(editor=window, tray_registered=False, tray_hold=False,
            stopping=False, hold=Mock(), release=Mock(), open_editor=Mock(), quit=Mock(),
            monitor=None, monitor_busy=False, device_lock=threading.Lock(), indicator=None)

    def close(self, window, application):
        window.get_application = lambda: application
        return self.app.Editor.on_close(window)

    def test_close_with_registered_icon_hides_and_preserves_unsaved_changes(self):
        window = self.window(dirty=True)
        application = self.application(window)
        application.tray_registered = True
        self.assertTrue(self.close(window, application))
        window.hide.assert_called_once()
        window.discard_ok.assert_not_called()
        self.assertTrue(window.dirty)

    def test_close_without_visible_tray_requires_discard_decision(self):
        window = self.window(dirty=True)
        application = self.application(window)
        window.discard_ok.return_value = False
        self.assertTrue(self.close(window, application))
        window.hide.assert_not_called()
        window.discard_ok.return_value = True
        self.assertFalse(self.close(window, application))

    def test_busy_device_operation_blocks_close_even_with_tray(self):
        window = self.window(busy=True)
        application = self.application(window)
        application.tray_registered = True
        self.assertTrue(self.close(window, application))
        window.error.assert_called_once()
        window.hide.assert_not_called()
        window.discard_ok.assert_not_called()

    def test_tray_registration_acquires_only_one_background_hold(self):
        application = self.application(self.window())
        self.app.Application.tray_changed(application, True)
        self.app.Application.tray_changed(application, True)
        application.hold.assert_called_once()
        self.assertTrue(application.tray_hold)

    def test_losing_tray_restores_editor_before_releasing_background_hold(self):
        window = self.window(dirty=True)
        application = self.application(window)
        application.tray_registered = application.tray_hold = True
        order = []
        application.open_editor.side_effect = lambda: order.append("open")
        application.release.side_effect = lambda: order.append("release")
        self.app.Application.tray_changed(application, False)
        self.assertEqual(order, ["open", "release"])
        self.assertFalse(application.tray_hold)
        self.assertIs(application.editor, window)
        self.assertTrue(window.dirty)

    def test_registration_callbacks_after_shutdown_cannot_reopen_or_hold(self):
        application = self.application(self.window())
        application.stopping = True
        self.app.Application.tray_changed(application, True)
        application.hold.assert_not_called()
        application.open_editor.assert_not_called()

    def test_menu_quit_is_deferred_and_cancel_keeps_dirty_editor(self):
        window = self.window(dirty=True)
        window.discard_ok.return_value = False
        application = self.application(window)
        callbacks = []
        notifier = StatusNotifier(Mock(), Mock(), lambda: self.app.Application.request_quit(application))
        notifier.GLib = SimpleNamespace(idle_add=lambda callback: callbacks.append(callback))
        notifier._queue_callback(notifier.menu.callbacks[notifier.menu.QUIT])
        application.quit.assert_not_called()
        self.assertEqual(len(callbacks), 1)
        self.assertFalse(callbacks[0]())
        application.open_editor.assert_called_once()
        window.discard_ok.assert_called_once()
        application.quit.assert_not_called()
        self.assertTrue(window.dirty)

    def test_confirmed_discard_allows_explicit_quit(self):
        window = self.window(dirty=True)
        application = self.application(window)
        self.app.Application.request_quit(application)
        window.discard_ok.assert_called_once()
        application.quit.assert_called_once()

    def test_busy_device_operation_blocks_explicit_quit(self):
        window = self.window(busy=True, dirty=True)
        application = self.application(window)
        self.app.Application.request_quit(application)
        window.error.assert_called_once()
        window.discard_ok.assert_not_called()
        application.quit.assert_not_called()


class ApplicationWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = load_application()

    def setUp(self):
        self.snapshot = MonitorSnapshot(transport="usb", battery_percent=83)
        self.application = SimpleNamespace(stopping=False, monitor_busy=False, editor=None,
            device_lock=threading.Lock(), monitor=Mock(), indicator=Mock())
        self.application.monitor.get_snapshot.return_value = self.snapshot
        self.application.monitor.poll.return_value = self.snapshot
        self.workers, self.callbacks = [], []
        def make_thread(*, target, **_):
            return SimpleNamespace(start=lambda: self.workers.append(target))
        self.thread_patch = patch.object(self.app.threading, "Thread", side_effect=make_thread)
        self.idle_patch = patch.object(self.app.GLib, "idle_add",
            side_effect=lambda function, *args: self.callbacks.append((function, args)))
        self.thread_patch.start()
        self.idle_patch.start()
        self.addCleanup(self.thread_patch.stop)
        self.addCleanup(self.idle_patch.stop)

    def finish(self):
        callback, args = self.callbacks.pop(0)
        return callback(*args)

    def test_busy_editor_does_not_start_monitor_worker(self):
        self.application.editor = SimpleNamespace(busy=True)
        self.app.Application.refresh_monitor(self.application, force=True)
        self.assertEqual(self.workers, [])
        self.assertFalse(self.application.monitor_busy)

    def test_repeated_refresh_does_not_start_overlapping_workers(self):
        self.app.Application.refresh_monitor(self.application)
        self.app.Application.refresh_monitor(self.application, force=True)
        self.assertEqual(len(self.workers), 1)

    def test_manual_refresh_runs_poll_off_main_loop_then_delivers_snapshot(self):
        self.app.Application.refresh_monitor(self.application, force=True)
        self.application.monitor.poll.assert_not_called()
        self.workers[0]()
        self.application.monitor.poll.assert_called_once_with(force=True)
        self.assertTrue(self.application.monitor_busy)
        self.application.indicator.update.assert_not_called()
        self.assertFalse(self.finish())
        self.assertFalse(self.application.monitor_busy)
        self.application.indicator.update.assert_called_once_with(self.snapshot)

    def test_lock_held_by_configuration_skips_status_read_and_releases_busy_flag(self):
        self.application.device_lock.acquire()
        self.addCleanup(self.application.device_lock.release)
        self.app.Application.refresh_monitor(self.application)
        self.workers[0]()
        self.application.monitor.poll.assert_not_called()
        self.finish()
        self.assertFalse(self.application.monitor_busy)
        self.application.indicator.update.assert_called_once_with(self.snapshot)

    def test_unexpected_poll_failure_retains_reading_and_allows_later_refresh(self):
        self.application.monitor.poll.side_effect = RuntimeError("unexpected failure")
        self.app.Application.refresh_monitor(self.application)
        self.workers[0]()
        self.assertFalse(self.application.device_lock.locked())
        self.finish()
        result = self.application.indicator.update.call_args.args[0]
        self.assertEqual(result.battery_percent, 83)
        self.assertTrue(result.stale)
        self.assertIsNotNone(result.error)
        self.assertFalse(self.application.monitor_busy)
        self.app.Application.refresh_monitor(self.application)
        self.assertEqual(len(self.workers), 2)

    def test_worker_finishing_after_shutdown_does_not_touch_closed_ui(self):
        self.app.Application.refresh_monitor(self.application)
        self.workers[0]()
        self.application.stopping = True
        self.finish()
        self.application.indicator.update.assert_not_called()
        self.assertFalse(self.application.monitor_busy)


if __name__ == "__main__":
    unittest.main()
