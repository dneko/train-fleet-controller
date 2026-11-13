import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fleet_ui import FleetApp
from fleet_setup import SetupDialog
from sensor_ui import SensorDialog
from test_adaptive_control import configuration
from test_waiting_ui import DemoBackend
from tk_helpers import begin_tk, close_demo


@unittest.skipUnless(os.environ.get('TRAINSTUFF_GUI_TESTS') == '1','Requires a desktop Tk session')
class AdaptiveUITests(unittest.TestCase):
    def setUp(self):
        begin_tk(self)
        import tkinter as tk
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = tk.Tk()
        self.config = configuration(True)
        self.app = FleetApp(self.root,self.config,Path(self.folder.name)/'test.json',True,DemoBackend)
        self.addCleanup(close_demo,self)
        self.root.update()
        for timer in self.root.tk.call('after','info'):
            self.root.after_cancel(timer)
        self.root.after = lambda *args:None
        self.now = 100.
        self.app.tracker.clock = lambda:self.now
        self.app.alarm.clock = lambda:self.now
        self.app.alarm.bell = Mock()
        self.app.supervisor.remove_from_track('b')

    def read(self, reader, seconds, uid='01020304'):
        self.now = 100+seconds
        self.app.events.put(('rfid',reader,json.dumps(dict(reader=reader,uid=uid,unix_ms=int(self.now*1000)))))
        self.app.poll()

    def tick(self):
        for tid,value in self.app.supervisor.tick().items():
            self.app.send(tid,value)
        self.app.refresh_run_status()
        self.root.update()

    def test_alarm_banner_red_card_silence_and_clear_after_detection(self):
        from tkinter import ttk
        self.read('0',0)
        self.read('0',1,'11121314')
        self.app.command('a',40)
        self.now += 81
        self.tick()
        self.assertEqual(self.app.alarm.bell.call_count,1)
        self.assertEqual(self.app.supervisor.throttle['a'],40)
        self.assertEqual(self.app.alarm_panel.winfo_manager(),'pack')
        self.assertIn('ALARM',self.app.alarm_text.get())
        with patch('fleet_ui.time.monotonic',return_value=.6):
            self.app.refresh_card_styles()
            self.assertEqual(ttk.Style(self.root).lookup('Train0.Card.TFrame','background'),'#b91c1c')
        self.app.alarm.silence()
        self.now += 4
        self.tick()
        self.assertEqual(self.app.alarm.bell.call_count,1)
        self.now += 16
        self.tick()
        self.assertEqual(self.app.supervisor.throttle['a'],0)
        self.assertTrue(self.app.supervisor.alarms)
        self.read('1',self.now-100+1)
        self.tick()
        self.assertFalse(self.app.supervisor.alarms)
        self.assertEqual(self.app.alarm_panel.winfo_manager(),'')

    def test_rear_event_does_not_reposition_front_and_updates_occupancy(self):
        self.read('0',0)
        self.read('0',1,'11121314')
        self.app.command('a',40)
        self.read('1',10)
        front = self.app.tracker.last_seen['a']
        self.assertIn('0',self.app.supervisor.occupied())
        self.read('1',12,'11121314')
        self.assertEqual(self.app.tracker.last_seen['a'],front)
        self.assertNotIn('0',self.app.supervisor.occupied())
        self.assertEqual(self.app.sensors.occupied_gates,{'1'})

    def test_rear_field_and_scanned_tag_role_assignment(self):
        dialog = SetupDialog(self.root,self.config,lambda _:None)
        self.assertEqual(dialog.pages['trains'][1]['rear_tag'].get(),'11121314')
        dialog.destroy()
        self.app.sensors.record_tag({'node':'test','uid':'21222324'})
        applied = []
        sensors = SensorDialog(self.root,self.app.sensors,lambda:self.config,applied.append,live=False)
        self.addCleanup(sensors.close)
        sensors.tags.selection_set('21222324')
        sensors.train.set('A (a)')
        sensors.tag_role.set('Rear')
        sensors.assign_tag()
        self.assertEqual(applied[0]['trains'][0]['rear_tag'],'21222324')
        self.assertEqual(applied[0]['trains'][0]['tags'],['01020304'])

    def test_demo_learning_does_not_persist_and_live_learning_does(self):
        self.read('0',0)
        self.read('0',1,'11121314')
        self.app.command('a',40)
        self.read('1',10)
        self.assertIsNotNone(self.app.tracker.learning.get('a','0'))
        self.app.save_map()
        self.assertNotIn('section_history',json.loads(self.app.path.read_text())['trains'][0])
        self.app.tracker.learning.persist = True
        self.app.tracker.learning.sync('a')
        self.app.config['view']['radii'] = True  # Unsaved UI preference must stay unsaved.
        self.app.demo = False  # Exercise only the file-writing path; backend remains fake.
        try:
            self.app.save_learning(force=True)
        finally:
            self.app.demo = True
        self.assertIn('section_history',json.loads(self.app.path.read_text())['trains'][0])
        self.assertFalse(json.loads(self.app.path.read_text())['view']['radii'])
        self.assertFalse(self.app.tracker.learning.dirty)

    def test_timing_controls_save_factor_and_following_gap(self):
        from tkinter import ttk, Toplevel
        self.app.timing_settings()
        dialog = next(w for w in self.root.winfo_children() if isinstance(w,Toplevel))
        entries = [w for w in dialog.winfo_children() if isinstance(w,ttk.Entry)]
        self.assertEqual(entries[0].get(),'2.5')
        entries[1].delete(0,'end')
        entries[1].insert(0,'4')
        next(w for w in dialog.winfo_children() if isinstance(w,ttk.Button) and w['text']=='Save').invoke()
        self.assertEqual(self.app.supervisor.stop_time_factor,2.5)
        self.assertEqual(self.app.supervisor.following_seconds,4)
        saved = json.loads(self.app.path.read_text())
        self.assertEqual(saved['automation']['stop_time_factor'],2.5)
        self.assertEqual(saved['automation']['following_seconds'],4)

    def test_demo_generates_rear_passage_after_train_length_has_moved(self):
        with patch('fleet_ui.time.monotonic',side_effect=lambda:self.now), patch('fleet_ui.time.time',side_effect=lambda:self.now):
            self.app.toggle_demo()
            self.app.poll()
            front = self.app.tracker.last_seen['a']
            self.assertIn('5',self.app.supervisor.occupied())
            self.now += 3
            self.app.poll()
            self.assertEqual(self.app.tracker.last_seen['a'],front)
            self.assertNotIn('5',self.app.supervisor.occupied())
            self.assertIn('a',self.app.tracker.rear_seen)
