"""Real Tk event-loop regressions. Run with TRAINSTUFF_GUI_TESTS=1."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fleet_ui import FleetApp, CARD_BACKGROUNDS
from tk_helpers import begin_tk, close_demo


class DemoBackend:
    def __init__(self, *args):
        pass
    def speed(self, *args):
        pass
    def waiting(self, *args):
        pass
    def close(self):
        pass
    def update_trains(self, *args):
        pass


@unittest.skipUnless(os.environ.get('TRAINSTUFF_GUI_TESTS') == '1', 'Requires a desktop Tk session')
class WaitingUITests(unittest.TestCase):
    def setUp(self):
        begin_tk(self)
        import tkinter as tk
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = tk.Tk()
        self.config = {
            'trains': [{'id': tid, 'name': tid.upper(), 'hub_name': tid, 'tags': []} for tid in ('a','b')],
            'readers': [{'id': str(i), 'name': str(i), 'distance_to_next_m': 2., 'x': 100+i*100, 'y': 100}
                        for i in range(4)],
        }
        self.app = FleetApp(self.root, self.config, Path(self.folder.name)/'config.json', True, DemoBackend)
        self.addCleanup(close_demo,self)
        self.root.update()
        for timer in self.root.tk.call('after','info'):
            self.root.after_cancel(timer)
        self.root.after = lambda *args: None
        now = self.app.tracker.clock()
        self.app.tracker.last_seen = {'a': ('0',now), 'b': ('2',now)}
        self.sup = self.app.supervisor

    def move_other(self, block):
        self.app.tracker.last_seen['b'] = (block,self.app.tracker.clock())

    def tick(self):
        for tid, value in self.sup.tick().items():
            self.app.send(tid,value)
        self.app.refresh_run_status()
        self.root.update()

    def test_automatic_display_updates_do_not_issue_manual_commands(self):
        self.sup.enabled = True
        self.sup.automatic['a'] = True
        self.app.auto_vars['a'].set(True)
        with patch.object(self.app,'command',wraps=self.app.command) as manual:
            self.tick()
            original = self.sup.throttle['a']
            self.assertGreater(original,0)
            self.move_other('1')
            self.tick()
            self.assertEqual(self.sup.throttle['a'],0)
            self.assertTrue(self.sup.waiting['a'])
            self.assertTrue(self.app.auto_vars['a'].get())
            self.assertTrue(self.sup.automatic['a'])
            self.move_other('2')
            self.tick()
            self.assertEqual(self.sup.throttle['a'],original)
            self.assertTrue(self.sup.automatic['a'])
            manual.assert_not_called()

    def test_manual_slider_input_still_leaves_automatic_mode(self):
        self.sup.automatic['a'] = True
        self.app.auto_vars['a'].set(True)
        # The Tcl widget command is the path used by Tk's mouse/key bindings.
        slider = self.app.sliders['a']
        slider.tk.call(slider._w,'set',35)
        self.root.update()
        self.assertFalse(self.sup.automatic['a'])
        self.assertFalse(self.app.auto_vars['a'].get())
        self.assertEqual(self.sup.throttle['a'],35)

    def test_manual_hold_flashes_then_offers_resume_when_clear(self):
        from tkinter import ttk
        self.app.command('a',40)
        self.move_other('1')
        self.tick()
        self.assertEqual(self.sup.throttle['a'],0)
        button = self.app.resume_buttons['a']
        self.assertEqual(button.winfo_manager(),'grid')
        self.assertTrue(button.instate(['disabled']))
        style = ttk.Style(self.root)
        with patch('fleet_ui.time.monotonic',return_value=.6):
            self.app.refresh_card_styles()
            self.assertEqual(style.lookup('Train0.Card.TFrame','background'),'#fef3c7')
        with patch('fleet_ui.time.monotonic',return_value=1.1):
            self.app.refresh_card_styles()
            self.assertEqual(style.lookup('Train0.Card.TFrame','background'),CARD_BACKGROUNDS[0])
        self.move_other('2')
        self.tick()
        self.assertFalse(button.instate(['disabled']))
        self.assertIn('Resume +40%',self.app.dispatch_labels['a'].get())
        with patch('fleet_ui.time.monotonic',return_value=1.6):
            self.app.refresh_card_styles()
            self.assertEqual(style.lookup('Train0.Card.TFrame','background'),'#dcfce7')
        button.invoke()
        self.root.update()
        self.assertEqual(self.sup.throttle['a'],40)
        self.assertFalse(self.sup.waiting['a'])
        self.assertFalse(self.sup.automatic['a'])
        self.assertEqual(button.winfo_manager(),'')
