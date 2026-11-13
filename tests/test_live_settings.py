import copy
import json
from pathlib import Path
import tempfile
import unittest

from fleet_automation import Supervisor
from fleet_model import load_config
from fleet_setup import save_config
from loop_tracking import LoopTracker
from train_settings import can_apply_live, merge_train_edits, update_train_runtime
from train_speed import SpeedWarnings


def configuration():
    return dict(trains=[dict(id=t, name=t.upper(), hub_name=t.upper(), tags=[uid],
                            max_throttle=70, start_throttle=25)
                        for t, uid in [('a', '01020304'), ('b', '11121314')]],
                readers=[dict(id=str(i), name=str(i), distance_to_next_m=2) for i in range(6)])


class LiveSettingsTests(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.now = 100
        self.tracker = LoopTracker(self.config, clock=lambda:self.now)
        self.sup = Supervisor(self.config, self.tracker)
        self.tracker.last_seen = {'a': ('0', self.now), 'b': ('3', self.now)}

    def test_edit_keeps_runtime_and_new_tag_works_immediately(self):
        self.sup.automatic['a'] = True
        self.sup.request('a', 40)
        self.sup.request('b', 30)
        self.tracker.learning.record('b', '3', 1, 10, 300)
        occupancy = self.sup.occupied()
        reservations = dict(self.sup.reservations)
        seen = dict(self.tracker.last_seen)
        edited = copy.deepcopy(self.config)
        edited['trains'][0].update(name='New name', tags=['21222324'], max_throttle=50)
        self.assertTrue(can_apply_live(self.config, edited))
        update_train_runtime(self.sup, edited)
        self.assertEqual(self.sup.throttle, dict(a=40, b=30))
        self.assertTrue(self.sup.automatic['a'])
        self.assertEqual(self.sup.occupied(), occupancy)
        self.assertEqual(self.sup.reservations, reservations)
        self.assertEqual(self.tracker.last_seen, seen)
        self.assertIs(self.tracker.learning.trains['a'], self.sup.trains['a'])
        self.assertEqual(self.config['trains'][0]['name'], 'New name')
        self.assertIsNotNone(self.tracker.learning.get('b', '3'))
        self.now += 1
        result = self.tracker.ingest(json.dumps(dict(reader='1', uid='21222324')), '1')
        self.assertEqual(result[0], 'a')
        self.assertEqual(self.tracker.last_seen['a'], ('1', self.now))
        self.assertNotIn('01020304', self.tracker.tags)

    def test_unknown_read_does_not_change_occupancy_or_motion(self):
        self.sup.request('a', 40)
        occupancy = self.sup.occupied()
        result = self.tracker.ingest(json.dumps(dict(reader='2', uid='FFFFFFFF')), '2')
        self.sup.observe(result[0])
        self.assertIsNone(result[0])
        self.assertEqual(self.sup.tick(), {})
        self.assertEqual(self.sup.throttle['a'], 40)
        self.assertEqual(self.sup.occupied(), occupancy)

    def test_dialog_rebases_only_user_edits(self):
        original = copy.deepcopy(self.config)
        edited = copy.deepcopy(original)
        edited['trains'][0]['tags'] = ['21222324']
        self.tracker.learning.record('a', '0', 1, 10, 300)
        self.config['trains'][0]['photo'] = 'new.png'
        self.config['trains'][1]['in_service'] = False
        self.config['view'] = dict(activity_log=True)
        merged = merge_train_edits(self.config, original, edited)
        self.assertEqual(merged['trains'][0]['tags'], ['21222324'])
        self.assertEqual(merged['trains'][0]['section_history'], self.config['trains'][0]['section_history'])
        self.assertEqual(merged['trains'][0]['photo'], 'new.png')
        self.assertFalse(merged['trains'][1]['in_service'])
        self.assertEqual(merged['view'], self.config['view'])

    def test_layout_roster_and_ownership_require_structural_apply(self):
        for mutate in (lambda c:c['readers'].reverse(),
                       lambda c:c['trains'].pop(),
                       lambda c:c['trains'][0].update(control_mode='external')):
            updated = copy.deepcopy(self.config)
            mutate(updated)
            self.assertFalse(can_apply_live(self.config, updated))

    def test_removed_rear_does_not_release_unconfirmed_blocks(self):
        edited = copy.deepcopy(self.config)
        edited['trains'][0]['rear_tag'] = '21222324'
        update_train_runtime(self.sup, edited)
        held = self.tracker.clearance.occupied('a')
        self.assertIn('5', held)
        edited = copy.deepcopy(self.config)
        edited['trains'][0].pop('rear_tag')
        update_train_runtime(self.sup, edited)
        self.assertEqual(self.tracker.clearance.occupied('a'), held)

    def test_duplicate_tag_validation_preserves_file_and_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fleet.json'
            save_config(path, self.config)
            edited = copy.deepcopy(self.config)
            edited['trains'][0]['tags'] = edited['trains'][1]['tags']
            with self.assertRaises(ValueError):
                save_config(path, edited)
            self.assertEqual(load_config(path), self.config)
            self.assertEqual(self.tracker.tags['01020304'], 'a')

    def test_recovery_preserves_auto_and_other_faults_but_manual_stays_zero(self):
        self.sup.request('a', 40)
        occupied = self.sup.occupied()
        self.sup.connection_state('a', False)
        self.assertEqual(self.sup.throttle['a'], 0)
        self.assertEqual(self.tracker.commands['a'], 0)
        self.assertIn('a', self.sup.faults)
        self.sup.connection_state('a', True)
        self.assertNotIn('a', self.sup.faults)
        self.assertEqual(self.sup.tick(), {})
        self.assertEqual(self.sup.occupied(), occupied)
        self.sup.enabled = self.sup.automatic['a'] = True
        self.sup.target = .08
        self.assertGreater(self.sup.tick()['a'], 0)
        self.sup.connection_state('a', False)
        self.sup.connection_state('a', True)
        self.assertGreater(self.sup.tick()['a'], 0)
        self.sup.faults['a'] = 'Missed checkpoint'
        self.sup.connection_state('a', False)
        self.sup.connection_state('a', True)
        self.assertEqual(self.sup.faults['a'], 'Missed checkpoint')
        self.assertEqual(self.sup.throttle['a'], 0)


class SpeedWarningTests(unittest.TestCase):
    setUp = LiveSettingsTests.setUp
    def test_warns_after_sustained_limit_and_clears_on_stop_or_recovery(self):
        monitor = SpeedWarnings()
        self.sup.enabled = self.sup.automatic['a'] = True
        self.sup.target = .12
        self.tracker.speed_per_throttle['a'] = .001
        self.sup.request('a', 70)
        self.assertEqual(monitor.warning(self.sup, 'a'), '')
        self.now += 6
        self.assertIn('maximum throttle', monitor.warning(self.sup, 'a'))
        self.sup.target = .06
        self.assertEqual(monitor.warning(self.sup, 'a'), '')
        self.sup.request('a', 0)
        self.assertEqual(monitor.warning(self.sup, 'a'), '')

    def test_intentional_slowing_does_not_warn(self):
        monitor = SpeedWarnings()
        self.sup.request('a', 40)
        self.tracker.speed_samples['a'] = dict(at=self.now, moving=10, ratio=.5)
        self.assertIn('measured speed', monitor.warning(self.sup, 'a'))
        self.sup.reasons['a'] = 'Slowing for B'
        self.assertEqual(monitor.warning(self.sup, 'a'), '')
        self.sup.reasons.clear()
        self.sup.cruise_reasons['a'] = 'Auto following B'
        self.assertEqual(monitor.warning(self.sup, 'a'), '')

    def test_checkpoint_measures_slowdown_before_updating_model(self):
        self.tracker.ingest(json.dumps(dict(reader='0', uid='01020304', unix_ms=100000)), '0')
        self.tracker.speed_per_throttle['a'] = .01
        self.sup.request('a', 20)
        self.now += 15
        self.tracker.ingest(json.dumps(dict(reader='1', uid='01020304', unix_ms=115000)), '1')
        self.assertAlmostEqual(self.tracker.speed_samples['a']['ratio'], 2/3)
        self.assertIn('measured speed', SpeedWarnings().warning(self.sup, 'a'))

    def test_overdue_warning_precedes_existing_alarm(self):
        self.sup.request('a', 40)
        self.now += 53  # Startup allowance for a 2m section is 40 seconds.
        self.assertIn('awaiting checkpoint', SpeedWarnings().warning(self.sup, 'a'))
        self.sup.update_timing()
        self.assertFalse(self.sup.alarms)
