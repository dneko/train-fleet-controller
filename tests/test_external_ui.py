"""External speed matching through real Tk controls; no hardware connections."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock

from fleet_setup import SetupDialog
from fleet_ui import FleetApp
from map_geometry import STUD_M
from test_external_control import configuration
from test_waiting_ui import DemoBackend
from tk_helpers import begin_tk, close_demo


@unittest.skipUnless(os.environ.get('TRAINSTUFF_GUI_TESTS') == '1', 'Requires a desktop Tk session')
class ExternalUITests(unittest.TestCase):
    def setUp(self):
        begin_tk(self)
        import tkinter as tk
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = tk.Tk()
        self.config = configuration()
        self.app = FleetApp(self.root, self.config, Path(self.folder.name)/'config.json', True, DemoBackend)
        self.addCleanup(close_demo,self)
        self.root.update()
        for timer in self.root.tk.call('after', 'info'):
            self.root.after_cancel(timer)
        self.root.after = lambda *args: None
        self.sup = self.app.supervisor
        self.app.demo_running = True
        now = self.app.tracker.clock()
        self.app.tracker.last_seen = {'a': ('0',now), 'b': ('2',now), 'p': ('4',now)}
        self.app.tracker.speed_per_throttle.update(a=.004,b=.002)
        self.app.tracker.speeds['p'] = .064
        self.app.backend.speed = MagicMock()
        self.app.backend.waiting = MagicMock()

    def tick(self):
        for tid, value in self.sup.tick().items():
            self.app.send(tid, value)
        self.app.refresh_run_status()
        self.root.update()

    def match(self):
        self.app.match_button.invoke()
        self.tick()

    def test_matching_locks_manual_controls_and_updates_without_changing_saved_target(self):
        self.match()
        self.assertTrue(self.app.auto_enabled.get())
        self.assertEqual(self.app.target_speed.get(),8)
        self.assertEqual(self.config['automation']['target_speed_studs_s'],10)
        self.assertAlmostEqual(self.sup.target,10*STUD_M)
        self.assertIn('8.0 studs/s measured',self.app.pace_status.get())
        for tid in ('a','b'):
            self.assertTrue(self.app.auto_vars[tid].get())
            self.assertTrue(self.app.auto_checks[tid].instate(['disabled']))
            self.assertEqual(str(self.app.sliders[tid]['state']),'disabled')
        original = self.sup.throttle['a']
        self.app.auto_vars['a'].set(False)
        self.app.set_auto('a')
        self.app.command('a',-90)
        self.assertTrue(self.sup.automatic['a'])
        self.assertTrue(self.app.auto_vars['a'].get())
        self.assertEqual(self.sup.throttle['a'],original)
        self.assertEqual(self.app.sliders['a'].get(),original)
        self.app.auto_max.set('5')
        self.app.set_auto_max()
        self.tick()
        self.assertEqual(self.app.target_speed.get(),5)
        self.assertEqual(self.config['automation']['target_speed_studs_s'],5)
        self.assertLessEqual(self.app.tracker.estimated_speed('a'),5*STUD_M)

    def test_operator_stop_latches_and_resume_returns_to_matching(self):
        self.match()
        self.app.stop('a')
        self.app.change_pace_train()
        self.tick()
        self.assertEqual(self.sup.throttle['a'],0)
        self.assertTrue(self.app.auto_vars['a'].get())
        button = self.app.resume_buttons['a']
        self.assertEqual(button.winfo_manager(),'grid')
        self.assertFalse(button.instate(['disabled']))
        self.assertIn('Stopped by operator',self.app.dispatch_labels['a'].get())
        button.invoke()
        self.tick()
        self.assertGreater(self.sup.throttle['a'],0)
        self.assertEqual(button.winfo_manager(),'')

    def test_external_controls_and_fleet_stop_never_command_external_train(self):
        self.match()
        for button, _ in self.app.card_buttons['p'][:2]:
            self.assertTrue(button.instate(['disabled']))
        self.assertFalse(self.app.auto_vars['p'].get())
        self.app.send('p',30)
        self.app.stop('p')
        self.app.stop_all()
        self.root.update()
        self.assertIsNone(self.sup.pace_train)
        self.assertFalse(self.app.match_external.get())
        self.assertFalse(self.app.auto_enabled.get())
        self.assertEqual(self.sup.throttle,dict(a=0,b=0,p=0))
        self.assertEqual(self.app.target_speed.get(),10)
        self.assertEqual(str(self.app.sliders['a']['state']),'normal')
        self.assertNotIn('p',self.app.tracker.commands)
        self.assertEqual(self.app.tracker.speeds['p'],.064)
        self.assertIn('p',self.app.tracker.last_seen)
        for method in (self.app.backend.speed,self.app.backend.waiting):
            self.assertTrue(all(call.args[0] != 'p' for call in method.call_args_list))

    def test_turning_matching_off_stops_followers_and_persists_only_selection(self):
        self.match()
        saved = json.loads(self.app.path.read_text())
        self.assertEqual(saved['automation']['pace_train_id'],'p')
        self.app.match_button.invoke()
        self.tick()
        self.assertFalse(self.sup.enabled)
        self.assertIsNone(self.sup.pace_train)
        self.assertEqual(self.sup.throttle['a'],0)
        self.assertEqual(self.app.target_speed.get(),10)
        self.app.build()
        self.root.update()
        self.assertEqual(self.app.pace_choice.get(),'Pace train (p)')
        self.assertFalse(self.app.match_external.get())
        self.assertFalse(self.app.supervisor.enabled)

    def test_setup_changes_ownership_and_clears_invalid_source(self):
        self.config['automation']['pace_train_id'] = 'p'
        dialog = SetupDialog(self.root,self.config,lambda _:None)
        self.addCleanup(dialog.destroy)
        dialog.refresh('trains',2)
        variables = dialog.pages['trains'][1]
        self.assertEqual(variables['control_mode'].get(),'External controller')
        self.assertEqual(variables['hub_name'].get(),'')
        variables['hub_name'].set('Pace hub')
        variables['control_mode'].set('Fleet controller')
        self.assertTrue(dialog.update_item('trains'))
        self.assertEqual(dialog.config_data['trains'][2]['control_mode'],'fleet')
        self.assertNotIn('pace_train_id',dialog.config_data['automation'])
        variables['control_mode'].set('External controller')
        variables['hub_name'].set('')
        self.assertTrue(dialog.update_item('trains'))
        self.assertEqual(dialog.config_data['trains'][2]['control_mode'],'external')

