import copy
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fleet_ui import FleetApp
from fleet_setup import save_config
from loop_tracking import motor_speed
import test_external_ui
from test_live_settings import configuration
from test_waiting_ui import DemoBackend
from tk_helpers import begin_tk, close_demo


@unittest.skipUnless(os.environ.get('TRAINSTUFF_GUI_TESTS') == '1', 'Requires a desktop Tk session')
class AutoAndAlarmUITests(unittest.TestCase):
    setUp = test_external_ui.ExternalUITests.setUp
    tick = test_external_ui.ExternalUITests.tick

    def test_override_to_auto_is_one_click_and_reports_blockers(self):
        self.app.alarm.bell = Mock()
        self.app.force_buttons['a'].invoke()
        button = self.app.auto_checks['a']
        self.assertFalse(button.instate(['disabled']))
        self.root.update()
        self.assertGreaterEqual(button.winfo_width(),70)
        self.assertGreaterEqual(button.winfo_height(),24)
        self.assertIn('override active',self.app.auto_readiness.get())
        self.app.backend.speed.reset_mock()
        button.invoke()
        self.assertNotIn('a',self.sup.forced)
        self.assertTrue(self.sup.automatic['a'])
        self.assertTrue(self.sup.enabled)
        self.assertTrue(self.app.auto_enabled.get())
        self.assertGreater(self.sup.throttle['a'],0)
        self.assertNotIn(('a',0),[c.args for c in self.app.backend.speed.call_args_list])
        self.app.force_buttons['a'].invoke()
        self.app.tracker.last_seen['b'] = self.app.tracker.last_seen['a']
        button.invoke()
        self.assertEqual(self.sup.throttle['a'],0)
        self.assertIn('occupied',self.app.auto_readiness.get())

    def test_paced_override_can_return_directly_to_auto(self):
        self.app.match_button.invoke()
        self.app.force_buttons['a'].invoke()
        self.assertFalse(self.app.auto_vars['a'].get())
        self.assertFalse(self.app.auto_checks['a'].instate(['disabled']))
        self.app.auto_checks['a'].invoke()
        self.assertNotIn('a',self.sup.forced)
        self.assertNotIn('a',self.sup.operator_paused)
        self.assertTrue(self.sup.automatic['a'])

    def test_alarm_settings_save_live_and_map_circles_survive_mute(self):
        self.app.alarm_sound.play = Mock()
        self.app.alarm.bell = self.app.alarm_sound.play
        self.app.backend.speed.reset_mock()
        tracker = self.app.tracker
        self.app.set_alarm_sound('none')
        self.assertEqual(json.loads(self.app.path.read_text())['alerts']['sound'], 'none')
        self.assertIs(self.app.tracker,tracker)
        self.app.backend.speed.assert_not_called()
        self.sup.alarms = {('a','front',('0',100)): 'A checkpoint overdue',
                           ('a','collision',('2',('a','b'))): 'Collision in green block'}
        self.app.refresh_run_status()
        self.app.draw()
        self.assertIn('SOUND OFF',self.app.alarm_heading['text'])
        self.assertEqual(len(self.app.canvas.find_withtag('alarm-marker')),2)
        self.assertEqual(len(self.app.canvas.find_withtag('alarm-checkpoint:1')),1)
        self.assertEqual(len(self.app.canvas.find_withtag('alarm-trains:a:b')),1)
        self.app.silence_alarm()
        self.app.draw()
        self.assertEqual(len(self.app.canvas.find_withtag('alarm-marker')),2)
        self.sup.alarms.clear()
        self.app.draw()
        self.assertFalse(self.app.canvas.find_withtag('alarm-marker'))

    def test_alarm_setting_save_failure_preserves_previous_selection(self):
        with patch('fleet_ui.save_config',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.app.set_alarm_sound('none')
        self.assertEqual(self.app.alarm_sound.mode,'siren')
        self.assertNotIn('alerts',self.app.config)

    def test_collision_circle_encloses_both_train_markers_and_follows_their_positions(self):
        self.sup.alarms = {('a','collision',('0',('a','b'))): 'Collision',
                           ('b','collision',('0',('a','b'))): 'Collision'}
        previous = None
        for zoom, offset, fraction, phase in ((1., [0.,0.], .1, .6), (1.8, [30.,60.], .8, 1.1)):
            self.app.zoom, self.app.offset = zoom, offset
            self.app.tracker.frozen['a'] = fraction
            with patch('fleet_ui.time.monotonic', return_value=phase):
                self.app.draw()
            canvas = self.app.canvas
            rings = canvas.find_withtag('alarm-marker')
            self.assertEqual(len(rings), 1)
            self.assertIn('alarm-trains:a:b', canvas.gettags(rings[0]))
            color = '#f97316' if phase == .6 else '#dc2626'
            self.assertEqual(canvas.itemcget(rings[0], 'outline'), color)
            self.assertEqual(canvas.itemcget(canvas.find_withtag('alarm-caption')[0], 'fill'), color)
            bounds = canvas.coords(rings[0])
            left, top, right, bottom = bounds
            x, y, radius = (left+right)/2, (top+bottom)/2, (right-left)/2
            self.assertAlmostEqual(right-left, bottom-top)
            for tid in ('a', 'b'):
                coords = canvas.coords(canvas.find_withtag(f'train:{tid}')[0])
                for px, py in zip(coords[::2], coords[1::2]):
                    self.assertLess(math.hypot(px-x, py-y), radius-2)
            if previous is not None:
                self.assertNotEqual(bounds, previous)
            previous = bounds
        self.app.silence_alarm()
        self.app.draw()
        self.assertEqual(len(self.app.canvas.find_withtag('alarm-marker')), 1)
        self.sup.alarms.clear()
        self.app.draw()
        self.assertFalse(self.app.canvas.find_withtag('alarm-marker'))


@unittest.skipUnless(os.environ.get('TRAINSTUFF_GUI_TESTS') == '1', 'Requires a desktop Tk session')
class MotorOnlyUITests(unittest.TestCase):
    def setUp(self):
        begin_tk(self)
        import tkinter as tk
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = tk.Tk()
        self.config = configuration()
        self.config['readers'] = []
        self.config['trains'][0]['forward_sign'] = -1
        self.path = Path(self.folder.name)/'motor.json'
        save_config(self.path,self.config)
        with patch('fleet_ui.load_reader_token') as token, patch('fleet_ui.create_server') as server:
            self.app = FleetApp(self.root,self.config,self.path,False,DemoBackend,motor_only=True)
            token.assert_not_called()
            server.assert_not_called()
        self.addCleanup(close_demo,self)
        self.root.update()
        for timer in self.root.tk.call('after','info'):
            self.root.after_cancel(timer)
        self.root.after = lambda *args:None
        self.app.backend.speed = Mock()
        self.app.backend.waiting = Mock()

    def test_motor_controls_have_no_map_sensor_or_auto_dependencies(self):
        self.assertFalse(hasattr(self.app,'canvas'))
        self.assertIsNone(self.app.server)
        self.assertIsNone(self.app.discovery)
        self.assertFalse(self.app.auto_checks['a'].winfo_viewable())
        self.assertFalse(self.app.force_buttons['a'].winfo_viewable())
        self.assertFalse(self.app.match_button.winfo_viewable())
        self.assertFalse(self.app.auto_master.winfo_viewable())
        self.app.events.put(('ready','a',True))
        self.app.poll()
        self.app.command('a',40)
        self.app.backend.speed.assert_called_with('a',-40)
        self.assertEqual(self.app.card_speeds['a'].get(),'Motor +40%')
        self.app.events.put(('rfid','0',json.dumps(dict(reader='0',uid='01020304'))))
        self.app.poll()
        self.assertFalse(self.app.tracker.last_seen)
        self.assertEqual(self.app.supervisor.throttle['a'],40)
        self.app.stop_all()
        self.assertEqual(self.app.supervisor.throttle['a'],0)

    def test_live_settings_and_configuration_reload_keep_motor_mode(self):
        self.app.events.put(('ready','a',True))
        self.app.poll()
        self.app.command('a',30)
        edited = copy.deepcopy(self.config)
        edited['trains'][0]['name'] = 'New A'
        self.app.apply_setup(edited)
        self.assertEqual(self.app.supervisor.throttle['a'],30)
        self.assertIn('New A',self.app.train_titles['a']['text'])
        self.app.events.put(('configured',copy.deepcopy(self.config)))
        with patch('fleet_ui.create_server') as server:
            self.app.poll()
            server.assert_not_called()
        self.assertTrue(self.app.motor_only)
        self.assertEqual(self.app.supervisor.throttle['a'],0)
        self.assertFalse(hasattr(self.app,'canvas'))
