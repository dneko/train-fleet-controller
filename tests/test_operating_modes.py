import json
import unittest

from alarm_locations import alarm_locations
from fleet_automation import Supervisor
from loop_tracking import LoopTracker
from test_live_settings import configuration
import test_force_and_collision


class OperatingModeTests(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.now = 100.
        self.tracker = LoopTracker(self.config, clock=lambda:self.now)
        self.sup = Supervisor(self.config, self.tracker)

    def read(self, reader='0'):
        result = self.tracker.ingest(json.dumps(dict(reader=reader, uid='01020304', unix_ms=int(self.now*1000))), reader)
        self.sup.observe(result[0])

    def test_manual_start_unlocated_acquires_position_without_stopping(self):
        self.assertEqual(self.sup.request('a', 40), 40)
        self.assertIn('awaiting RFID', self.sup.automatic_status('a'))
        self.assertFalse(self.sup.occupied())
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'], 40)
        self.now += 2
        self.read()
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'], 40)
        self.assertEqual(self.sup.occupied()['0'], {'a'})
        self.assertFalse(self.sup.forced)
        self.assertFalse(self.sup.automatic['a'])

    def test_first_read_restores_known_block_checks(self):
        self.tracker.last_seen['b'] = ('1', 100)
        self.sup.request('a', 40)
        self.now += 2
        self.read()
        self.assertEqual(self.sup.tick()['a'], 0)
        self.assertTrue(self.sup.waiting['a'])
        self.assertIn('occupied', self.sup.reasons['a'])

    def test_auto_requires_positions_and_reports_all_missing_prerequisites(self):
        self.sup.ready['a'] = False
        self.sup.force_run('b', 35)
        issues = '\n'.join(self.sup.auto_issues())
        for phrase in ('Auto fleet is off', 'hub not ready', 'awaiting first RFID', 'override active', 'Target speed is zero'):
            self.assertIn(phrase, issues)
        self.sup.ready['a'] = True
        self.sup.target = .08
        self.sup.select_auto('a')
        self.assertEqual(self.sup.tick().get('a', 0), 0)
        self.assertEqual(self.sup.throttle['a'], 0)

    def test_override_to_auto_uses_normal_guards_immediately(self):
        self.tracker.last_seen = {'a': ('0', 100), 'b': ('3', 100)}
        self.tracker.speed_per_throttle['a'] = .004
        self.sup.target = .08
        self.sup.force_run('a', 90)
        self.sup.select_auto('a')
        self.assertEqual(self.sup.tick()['a'], 20)
        self.assertNotIn('a', self.sup.forced)
        self.assertTrue(self.sup.enabled)
        self.sup.force_run('a', 90)
        self.tracker.last_seen['b'] = ('0', 100)
        self.sup.select_auto('a')
        self.assertEqual(self.sup.tick()['a'], 0)
        self.assertTrue(self.sup.alarms)

    def test_motor_only_never_uses_tracking_or_auto(self):
        self.config['readers'] = []
        tracker = LoopTracker(self.config)
        sup = Supervisor(self.config, tracker, motor_only=True)
        sup.faults['a'] = 'Old fault'
        sup.on_track['a'] = False
        self.assertEqual(sup.request('a', -90), -70)
        sup.select_auto('a')
        self.assertFalse(sup.automatic['a'])
        self.assertEqual(sup.tick(), {})
        self.assertFalse(tracker.last_seen)
        self.assertFalse(tracker.commands)
        sup.connection_state('a', False)
        self.assertEqual(sup.throttle['a'], 0)
        sup.connection_state('a', True)
        self.assertEqual(sup.tick(), {})
        self.assertEqual(sup.request('a', 30), 30)
        self.assertEqual(sup.request('a', 0), 0)

    def test_alarm_locations_use_expected_checkpoint_and_shared_trains(self):
        self.tracker.last_seen = {'a': ('0', 100), 'b': ('3', 100)}
        self.sup.alarms = {('a','front',('0',100)): 'Front overdue',
                           ('b','rear',('2',100)): 'Rear overdue',
                           ('a','collision',('0',('a','b'))): 'Collision',
                           ('b','collision',('0',('a','b'))): 'Collision'}
        locations = alarm_locations(self.sup)
        self.assertEqual(set(locations), {('checkpoint','1'),('checkpoint','2'),('trains',('a','b'))})
        self.sup.alarms['b','collision',('3',('b','a'))] = 'Same trains in another block'
        self.assertEqual(set(alarm_locations(self.sup)), set(locations))
        self.tracker.directions['a'] = -1
        self.assertIn(('checkpoint','0'), alarm_locations(self.sup))
        self.sup.alarms.clear()
        self.assertFalse(alarm_locations(self.sup))


class ControlledFollowerTests(unittest.TestCase):
    setUp = test_force_and_collision.OverrideTests.setUp
    read = test_force_and_collision.OverrideTests.read

    def test_controlled_follower_enters_from_behind_leader_keeps_running(self):
        self.sup.trains['b']['tags'] = ['21222324']
        self.tracker.tags['21222324'] = 'b'
        self.tracker.last_seen['p'] = ('3',100)
        self.read('b','5',0)
        self.sup.request('a',30)
        self.tracker.speed_per_throttle['b'] = .004
        self.assertEqual(self.sup.request('b',30),30)
        self.read('b','0',5)
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],30)
        self.assertEqual(self.sup.throttle['b'],0)
        self.assertTrue(self.sup.alarms)
        self.assertIn('continuing ahead',self.sup.automatic_status('a'))
        self.assertEqual(self.sup.occupied()['0'], {'a','b'})
        self.read('a','1',6)
        self.sup.tick()
        self.assertFalse(self.sup.alarms)

    def test_controlled_head_on_still_holds_both(self):
        self.sup.request('a',30)
        self.tracker.last_seen['b'] = ('0',105)
        self.tracker.directions['b'] = -1
        self.sup.observe('b')
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],0)
        self.assertTrue(self.sup.alarms)
