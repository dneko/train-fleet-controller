import copy
import json
import os
import time
import unittest
from unittest.mock import Mock, patch

from battery_monitor import BatteryReading
import test_external_ui


@unittest.skipUnless(os.environ.get('TRAINSTUFF_GUI_TESTS') == '1', 'Requires a desktop Tk session')
class LiveSettingsUITests(unittest.TestCase):
    setUp = test_external_ui.ExternalUITests.setUp
    tick = test_external_ui.ExternalUITests.tick

    def test_train_settings_apply_without_rebuilding_or_stopping(self):
        self.app.command('a', 20)
        self.app.command('b', 20)
        tracker = self.app.tracker
        supervisor = self.app.supervisor
        seen = dict(tracker.last_seen)
        self.app.backend.reconfigure = Mock()
        self.app.backend.update_trains = Mock()
        self.app.backend.speed.reset_mock()
        self.app.setup('a')
        dialog = self.app.setup_dialog
        self.assertEqual(dialog.title(), 'Train settings')
        self.assertNotIn('readers', dialog.pages)
        fields = dialog.pages['trains'][1]
        fields['tags'].set('21222324')
        fields['name'].set('Updated A')
        dialog.apply()
        self.assertFalse(dialog.winfo_exists())
        self.assertIs(self.app.tracker, tracker)
        self.assertIs(self.app.supervisor, supervisor)
        self.assertEqual(tracker.last_seen, seen)
        self.app.backend.reconfigure.assert_not_called()
        self.app.backend.speed.assert_not_called()
        self.app.backend.update_trains.assert_called_once()
        self.assertEqual(self.app.supervisor.throttle['b'], 20)
        saved = json.loads(self.app.path.read_text())
        self.assertEqual(saved['trains'][0]['tags'], ['21222324'])
        self.assertEqual(self.app.train_titles['a']['text'], '●  Updated A')

    def test_failed_save_does_not_touch_moving_fleet(self):
        self.app.command('a', 20)
        self.app.backend.speed.reset_mock()
        before = copy.deepcopy(self.app.config)
        edited = copy.deepcopy(before)
        edited['trains'][0]['forward_sign'] = -1
        with patch('fleet_ui.save_config', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.app.apply_setup(edited)
        self.assertEqual(self.app.config, before)
        self.assertEqual(self.app.supervisor.throttle['a'], 20)
        self.app.backend.speed.assert_not_called()

    def test_polarity_change_only_stops_edited_train(self):
        self.app.command('a', 20)
        self.app.command('b', 20)
        edited = copy.deepcopy(self.app.config)
        edited['trains'][0]['forward_sign'] = -1
        self.app.backend.speed.reset_mock()
        self.app.apply_setup(edited)
        self.assertEqual(self.app.supervisor.throttle['a'], 0)
        self.assertEqual(self.app.supervisor.throttle['b'], 20)
        self.assertEqual([c.args for c in self.app.backend.speed.call_args_list], [('a', 0)])

    def test_power_warning_colors_text_and_four_card_fit(self):
        self.app.config['trains'].append(dict(id='d',name='D',hub_name='D',tags=[]))
        self.app.build()
        self.app.demo = False
        now = time.monotonic()
        self.app.batteries.update(a=BatteryReading(5800, 250, now), b=BatteryReading(5400, 300, now))
        self.app.refresh_run_status()
        self.app.refresh_batteries()
        self.root.update()
        self.assertEqual(self.app.battery_labels['a']['text'], '5.80 V · 0.250 A · 1.45 W')
        self.assertEqual(str(self.app.battery_labels['a']['background']), '#fef08a')
        self.assertEqual(str(self.app.battery_labels['b']['background']), '#fee2e2')
        self.assertIn('power is getting low', self.app.dispatch_labels['a'].get())
        self.assertIn('imminent', self.app.dispatch_labels['b'].get())
        for tid, card in self.app.control_cards.items():
            label = self.app.battery_labels[tid]
            self.assertLessEqual(label.winfo_reqwidth(), label.winfo_width())
            self.assertLessEqual(card.winfo_rooty()+card.winfo_height(), self.root.winfo_rooty()+self.root.winfo_height())
            reason = self.app.reason_labels[tid]
            self.assertLessEqual(reason.winfo_rooty()+reason.winfo_height(), card.winfo_rooty()+card.winfo_height())
            self.assertLessEqual(reason.winfo_reqheight(), reason.winfo_height())

    def test_simultaneous_speed_and_battery_warnings_fit(self):
        self.app.demo = False
        self.app.supervisor.ready['a'] = True
        self.app.command('a', 20)
        self.app.batteries['a'] = BatteryReading(5400, 250, time.monotonic())
        self.app.tracker.speed_samples['a'] = dict(at=self.app.tracker.clock(), moving=10, ratio=.5)
        self.app.refresh_run_status()
        self.app.refresh_batteries()
        self.root.update()
        self.assertIn('imminent', self.app.dispatch_labels['a'].get())
        self.assertIn('unable to maintain speed', self.app.dispatch_labels['a'].get())
        self.assertIn('slow', self.app.card_speeds['a'].get())
        label = self.app.reason_labels['a']
        self.assertLessEqual(label.winfo_reqheight(), label.winfo_height())

    def test_ordinary_fleet_setup_tag_edit_stays_live_with_precise_distances(self):
        self.app.config['readers'][0]['distance_to_next_m'] = 2.123456789
        self.app.backend.reconfigure = Mock()
        self.app.setup()
        dialog = self.app.setup_dialog
        dialog.pages['trains'][1]['tags'].set('21222324')
        dialog.apply()
        self.app.backend.reconfigure.assert_not_called()
        self.assertFalse(self.app.applying)

    def test_batched_connection_loss_and_ready_do_not_restore_manual_throttle(self):
        self.app.command('a', 20)
        self.app.events.put(('ready', 'a', False))
        self.app.events.put(('ready', 'a', True))
        self.app.demo_running = False
        self.app.poll()
        self.assertTrue(self.app.supervisor.ready['a'])
        self.assertEqual(self.app.supervisor.throttle['a'], 0)
        self.assertEqual(self.app.sliders['a'].get(), 0)
        self.assertNotIn('a', self.app.supervisor.faults)
