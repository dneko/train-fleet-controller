"""Real mouse/keyboard routing and emergency stops, without hardware connections."""
import os
from pathlib import Path
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from fleet_ui import FleetApp
from test_external_control import configuration
from test_waiting_ui import DemoBackend
from tk_helpers import begin_tk, close_demo


@unittest.skipUnless(os.environ.get('TRAINSTUFF_GUI_TESTS') == '1', 'Requires a desktop Tk session')
class TrainControlsUITests(unittest.TestCase):
    mode = 'demo'

    def setUp(self):
        begin_tk(self)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = tk.Tk()
        self.config = configuration()
        if self.mode == 'motor':
            self.config['readers'] = []
        with patch('fleet_ui.load_reader_token', return_value=''):
            self.app = FleetApp(self.root, self.config, Path(self.folder.name)/'config.json',
                                self.mode == 'demo', DemoBackend, motor_only=self.mode == 'motor')
        self.addCleanup(close_demo, self)
        self.root.update()
        for timer in self.root.tk.call('after', 'info'):
            self.root.after_cancel(timer)
        self.root.after = lambda *args: None
        self.root.focus_force()
        self.app.backend.speed = Mock()
        self.app.backend.waiting = Mock()
        self.app.alarm.bell = Mock()
        self.app.supervisor.ready.update(a=True, b=True)
        if self.mode != 'motor':
            now = self.app.tracker.clock()
            self.app.tracker.last_seen = {'a': ('0', now), 'b': ('2', now)}

    def click(self, widget):
        widget.event_generate('<ButtonPress-1>', x=3, y=3)
        widget.event_generate('<ButtonRelease-1>', x=3, y=3)
        self.root.update()

    def key(self, key):
        self.root.focus_get().event_generate(f'<KeyPress-{key}>')
        self.root.update()

    def test_click_card_or_nested_label_selects_only_that_train(self):
        self.app.control_cards['a'].focus_set()
        self.root.update()
        self.key('Up')
        self.app.backend.speed.assert_not_called()
        self.click(self.app.train_titles['a'])
        self.assertEqual(self.app.active_train, 'a')
        self.key('Up')
        self.key('Right')
        self.assertEqual(self.app.supervisor.throttle['a'], 2)
        self.key('Down')
        self.key('Left')
        self.key('Down')
        self.assertEqual(self.app.supervisor.throttle['a'], -1)
        self.click(self.app.speed_labels['b'])
        self.key('Up')
        self.assertEqual(self.app.supervisor.throttle['b'], 1)
        self.assertEqual(self.app.supervisor.throttle['a'], -1)
        style = ttk.Style(self.root)
        self.assertEqual(style.lookup('Train1.ControlCard.TFrame', 'bordercolor'), '#2563eb')
        self.assertNotEqual(style.lookup('Train0.ControlCard.TFrame', 'bordercolor'), '#2563eb')

    def test_slider_focus_has_one_step_and_existing_throttle_limits(self):
        slider = self.app.sliders['a']
        x, y = slider.coords(0)
        slider.event_generate('<ButtonPress-1>', x=x, y=y)
        slider.event_generate('<ButtonRelease-1>', x=x, y=y)
        slider.focus_set()
        self.root.update()
        self.assertEqual(self.app.active_train, 'a')
        self.app.backend.speed.reset_mock()
        self.key('Up')
        self.assertEqual(slider.get(), 1)
        self.app.backend.speed.assert_called_once_with('a', 1)
        self.app.command('a', 70)
        self.key('Up')
        self.assertEqual(slider.get(), 70)
        self.app.command('a', -70)
        self.key('Down')
        self.assertEqual(slider.get(), -70)

    def test_other_controls_and_dialogs_keep_their_arrow_keys(self):
        self.click(self.app.control_cards['a'])
        entry = ttk.Entry(self.root)
        entry.place(x=400, y=10, width=120)
        entry.insert(0, '123')
        self.root.update()
        entry.focus_set()
        self.root.update()
        self.key('Left')
        self.key('Up')
        self.app.backend.speed.assert_not_called()
        dialog = tk.Toplevel(self.root)
        field = ttk.Entry(dialog)
        field.pack()
        dialog.grab_set()
        field.focus_set()
        self.root.update()
        self.key('Down')
        self.app.backend.speed.assert_not_called()
        dialog.destroy()
        entry.destroy()

    def test_external_unready_uploading_and_disabled_trains_stay_stopped(self):
        self.click(self.app.control_cards['p'])
        self.key('Up')
        self.app.backend.speed.assert_not_called()
        self.click(self.app.control_cards['a'])
        self.app.supervisor.ready['a'] = False
        self.key('Up')
        self.assertEqual(self.app.supervisor.throttle['a'], 0)
        self.app.supervisor.ready['a'] = True
        self.app.program_uploads.add('a')
        self.app.refresh_run_status()
        self.app.backend.speed.reset_mock()
        self.key('Up')
        self.app.backend.speed.assert_not_called()
        self.app.program_uploads.clear()
        self.app.refresh_run_status()
        self.app.applying = True
        self.key('Up')
        self.app.backend.speed.assert_not_called()
        self.app.applying = False
        if self.mode != 'motor':
            self.app.supervisor.set_pace_train('p')
            self.app.refresh_run_status()
            self.key('Up')
            self.app.backend.speed.assert_not_called()

    def test_emergency_button_stops_all_and_cancels_automatic_and_force_modes(self):
        self.app.command('a', 30)
        self.app.command('b', -20)
        if self.mode != 'motor':
            self.app.supervisor.set_pace_train('p')
            self.app.match_external.set(True)
            self.app.toggle_force('a')
            self.app.auto_enabled.set(True)
        self.app.demo_running = self.mode == 'demo'
        self.app.refresh_run_status()
        self.root.update()
        button = self.app.emergency_stop_button
        self.assertTrue(button.winfo_viewable())
        self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth())
        self.assertLessEqual(button.winfo_rootx()+button.winfo_width(),
                             self.root.winfo_rootx()+self.root.winfo_width())
        if self.mode == 'demo':
            for widget in (self.app.demo_button, self.app.demo_badge):
                self.assertTrue(widget.winfo_viewable())
                self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth())
        button.invoke()
        self.root.update()
        self.assertEqual(self.app.supervisor.throttle, dict(a=0, b=0, p=0))
        self.assertFalse(self.app.supervisor.enabled)
        self.assertFalse(any(self.app.supervisor.automatic.values()))
        self.assertFalse(self.app.supervisor.forced)
        self.assertIsNone(self.app.supervisor.pace_train)
        self.assertFalse(self.app.demo_running)
        self.assertFalse(self.app.match_external.get())
        self.assertFalse(self.app.auto_enabled.get())
        for tid in ('a', 'b'):
            self.assertEqual(self.app.sliders[tid].get(), 0)
            self.assertIn((tid, 0), [call.args for call in self.app.backend.speed.call_args_list])
        self.assertTrue(all(call.args[0] != 'p' for call in self.app.backend.speed.call_args_list))

    def test_rebuild_requires_new_selection_and_does_not_duplicate_key_bindings(self):
        self.click(self.app.control_cards['a'])
        self.app.build()
        self.app.supervisor.ready['a'] = True
        self.root.update()
        self.assertIsNone(self.app.active_train)
        self.click(self.app.train_titles['a'])
        self.key('Up')
        self.assertEqual(self.app.supervisor.throttle['a'], 1)


class LiveTrainControlsUITests(TrainControlsUITests):
    mode = 'live'


class MotorTrainControlsUITests(TrainControlsUITests):
    mode = 'motor'
