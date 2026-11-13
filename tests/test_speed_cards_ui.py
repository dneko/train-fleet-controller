import os
import unittest
from unittest.mock import Mock, patch

import test_external_ui


@unittest.skipUnless(os.environ.get('TRAINSTUFF_GUI_TESTS') == '1','Requires a desktop Tk session')
class SpeedCardUITests(unittest.TestCase):
    tick = test_external_ui.ExternalUITests.tick

    def setUp(self):
        test_external_ui.ExternalUITests.setUp(self)
        self.app.alarm.bell = Mock()

    def test_both_types_show_speed_even_when_extra_information_is_hidden(self):
        self.app.command('a',20)
        self.app.set_view_details(False)
        self.tick()
        self.assertEqual(self.app.card_speeds['a'].get(),'10.0 studs/s · est.')
        self.assertEqual(self.app.card_speeds['p'].get(),'8.0 studs/s · avg.')
        for tid in ('a','p'):
            self.assertTrue(self.app.speed_labels[tid].winfo_viewable())
        self.app.command('a',10)
        self.tick()
        self.assertEqual(self.app.card_speeds['a'].get(),'5.0 studs/s · est.')
        self.app.stop('a')
        self.tick()
        self.assertEqual(self.app.card_speeds['a'].get(),'0.0 studs/s · stopped')
        self.assertEqual(self.app.card_speeds['p'].get(),'8.0 studs/s · avg.')

    def test_four_cards_fit_with_readable_speed_rows(self):
        self.config['trains'].append(dict(id='d',name='D',hub_name='D',tags=[]))
        self.app.build()
        self.root.update()
        for tid,card in self.app.control_cards.items():
            speed = self.app.speed_labels[tid]
            self.assertGreaterEqual(speed.winfo_height(),20)
            self.assertGreater(speed.winfo_width(),150)
            self.assertLessEqual(speed.winfo_rooty()+speed.winfo_height(),card.winfo_rooty()+card.winfo_height())
            self.assertLessEqual(card.winfo_rooty()+card.winfo_height(),self.root.winfo_rooty()+self.root.winfo_height())

    def test_alarm_banner_flashes_and_title_clearly_marks_incident(self):
        key = ('a','front',('0',100))
        self.sup.alarms = {key:'A · Blue block · checkpoint overdue'}
        with patch('fleet_ui.time.monotonic',return_value=.6):
            self.app.refresh_run_status()
            self.root.update()
            self.assertEqual(self.app.alarm_panel['background'],'#fef08a')
            self.assertTrue(self.app.alarm_heading.winfo_viewable())
            self.assertIn('TRAIN ALARM',self.root.title())
        self.app.silence_alarm()
        self.assertIn('SOUND SILENCED',self.app.alarm_heading['text'])
        self.assertEqual(self.app.alarm_panel['background'],'#b91c1c')
        self.sup.alarms.clear()
        self.app.refresh_alarms()
        self.assertNotIn('TRAIN ALARM',self.root.title())
