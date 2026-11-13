import json
import os
import unittest
from unittest.mock import Mock

import test_external_ui


@unittest.skipUnless(os.environ.get('TRAINSTUFF_GUI_TESTS') == '1','Requires a desktop Tk session')
class ForceUITests(unittest.TestCase):
    tick = test_external_ui.ExternalUITests.tick

    def setUp(self):
        test_external_ui.ExternalUITests.setUp(self)
        self.app.alarm.bell = Mock()
        self.sup = self.app.supervisor

    def test_force_button_runs_held_speed_and_slider_then_end_force_stops(self):
        from tkinter import ttk
        self.app.tracker.last_seen['b'] = ('1',self.app.tracker.clock())
        self.app.tracker.frozen['a'] = .95
        self.app.command('a',40)
        self.assertEqual(self.sup.throttle['a'],0)
        button = self.app.force_buttons['a']
        self.assertFalse(button.instate(['disabled']))
        button.invoke()
        self.tick()
        self.assertEqual(self.sup.throttle['a'],40)
        self.assertEqual(button['text'],'End force')
        self.assertIn('FORCE RUN',self.app.dispatch_labels['a'].get())
        self.assertEqual(ttk.Style(self.root).lookup('Train0.Card.TFrame','background'),'#ffedd5')
        slider = self.app.sliders['a']
        slider.tk.call(slider._w,'set',-45)
        self.root.update()
        self.assertEqual(self.sup.forced['a'],-45)
        button.invoke()
        self.tick()
        self.assertEqual(self.sup.throttle['a'],0)
        self.assertNotIn('a',self.sup.forced)
        self.assertEqual(button['text'],'Force run')
        self.app.backend.speed.assert_called_with('a',0)

    def test_force_can_override_matching_and_stop_fleet_cancels_every_override(self):
        self.app.match_button.invoke()
        self.tick()
        self.app.force_buttons['a'].invoke()
        self.app.command('a',90)
        self.tick()
        self.assertEqual(self.sup.throttle['a'],90)
        self.assertFalse(self.app.auto_vars['a'].get())
        self.assertEqual(str(self.app.sliders['a']['state']),'normal')
        self.assertFalse(self.app.auto_checks['a'].instate(['disabled']))
        self.app.force_buttons['b'].invoke()
        self.app.stop_all()
        self.tick()
        self.assertFalse(self.sup.forced)
        self.assertEqual(self.sup.throttle,dict(a=0,b=0,p=0))
        self.assertTrue(self.app.force_buttons['p'].instate(['disabled']))
        self.assertTrue(all(call.args[0] != 'p' for call in self.app.backend.speed.call_args_list))

    def test_stop_button_and_zero_slider_cancel_force(self):
        self.app.force_buttons['a'].invoke()
        self.app.stop('a')
        self.tick()
        self.assertNotIn('a',self.sup.forced)
        self.assertEqual(self.sup.throttle['a'],0)
        self.app.force_buttons['a'].invoke()
        self.app.command('a',0)
        self.tick()
        self.assertNotIn('a',self.sup.forced)
        self.assertEqual(self.sup.throttle['a'],0)

    def test_incoming_external_read_sounds_alarm_without_stopping_leader(self):
        tracker = self.app.tracker
        now = tracker.clock()
        tracker.last_seen['p'] = ('5',now)
        tracker.last_seen['a'] = ('0',now-3)
        self.app.command('a',30)
        self.app.demo_running = False  # Feed the actual receiver queue without simulated movement.
        self.app.events.put(('rfid','0',json.dumps(dict(reader='0',uid='01020304',unix_ms=100000))))
        self.app.poll()
        self.app.demo_running = True
        self.tick()
        self.assertEqual(self.sup.throttle['a'],30)
        self.assertIn('POTENTIAL COLLISION',self.app.alarm_text.get())
        self.assertIn('Blue block',self.app.alarm_text.get())
        self.assertEqual(self.app.alarm_text.get().count('POTENTIAL COLLISION'),1)
        self.app.alarm.bell.assert_called_once()
        self.assertIn('continuing ahead',self.app.dispatch_labels['a'].get())
        self.assertEqual(self.sup.external_reservations()['1'],'a')
        self.app.silence_alarm()
        self.tick()
        self.assertEqual(self.sup.throttle['a'],30)
        self.assertTrue(self.sup.alarms)

    def test_override_is_not_restored_by_rebuilding_and_not_ready_button_is_disabled(self):
        self.app.force_buttons['a'].invoke()
        self.app.stop_all()
        self.app.build()
        self.root.update()
        self.assertFalse(self.app.supervisor.forced)
        self.app.supervisor.ready['a'] = False
        self.app.refresh_run_status()
        self.assertTrue(self.app.force_buttons['a'].instate(['disabled']))
