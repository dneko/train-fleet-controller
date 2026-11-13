import json
import unittest

from fleet_automation import Supervisor
from loop_tracking import LoopTracker
from test_external_control import configuration
from train_speed import card_speed


class OverrideTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.config = configuration()
        self.config['trains'][0]['tags'] = ['11121314']
        self.tracker = LoopTracker(self.config,clock=lambda:self.now)
        self.sup = Supervisor(self.config,self.tracker)
        self.tracker.last_seen = {'a':('0',self.now),'b':('3',self.now),'p':('5',self.now)}
        self.tracker.speed_per_throttle['a'] = .004

    def read(self, tid, reader, seconds):
        self.now = 100+seconds
        previous = self.tracker.last_seen.get(tid,(None,None))[0]
        uid = self.sup.trains[tid]['tags'][0]
        result = self.tracker.ingest(json.dumps(dict(reader=reader,uid=uid,unix_ms=int(self.now*1000))),reader)
        if result:
            self.sup.observe(tid,previous)
        return result

    def test_force_uses_held_throttle_and_bypasses_occupied_block(self):
        self.tracker.last_seen['b'] = ('1',self.now)
        self.tracker.frozen['a'] = .95
        self.assertEqual(self.sup.request('a',40),0)
        self.assertEqual(self.sup.force_run('a'),40)
        for _ in range(5):
            self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],40)
        self.assertFalse(self.sup.waiting['a'])
        self.assertEqual(self.sup.occupied()['1'],{'b'})
        self.assertIn('FORCE RUN',self.sup.automatic_status('a'))

    def test_force_bypasses_unknown_positions_faults_service_and_automatic_limits(self):
        self.sup.remove_from_track('a')
        self.tracker.last_seen.pop('b')
        self.sup.faults['p'] = 'Missed checkpoint'
        self.sup.unidentified = True
        self.sup.max_speed = .001
        self.assertEqual(self.sup.force_run('a',95),95)
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],95)
        self.assertFalse(self.sup.on_track['a'])
        self.assertFalse(self.sup.service['a'])
        self.assertEqual(self.sup.request('a',180),100)

    def test_force_allows_reverse_and_still_restricts_protocol_to_100(self):
        self.tracker.clearance.enabled.add('a')
        self.assertEqual(self.sup.request('a',-35),0)
        self.assertEqual(self.sup.force_run('a'),-35)
        self.assertEqual(self.sup.request('a',-140),-100)
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],-100)

    def test_stop_and_end_force_cancel_override_and_cannot_restart_by_tick(self):
        self.sup.force_run('a',35)
        self.sup.request('a',0)
        self.sup.tick()
        self.assertNotIn('a',self.sup.forced)
        self.assertEqual(self.sup.throttle['a'],0)
        self.sup.force_run('a',35)
        self.sup.end_force('a')
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],0)
        rebuilt = Supervisor(self.config,LoopTracker(self.config))
        self.assertFalse(rebuilt.forced)

    def test_force_during_matching_ignores_pace_but_end_requires_resume(self):
        self.tracker.speeds['p'] = .08
        self.sup.set_pace_train('p')
        self.sup.force_run('a',60)
        self.tracker.speeds.pop('p')
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],60)
        self.assertTrue(self.sup.automatic['a'])
        self.sup.end_force('a')
        self.tracker.speeds['p'] = .08
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],0)
        self.assertIn('a',self.sup.operator_paused)
        self.sup.resume('a')
        self.sup.tick()
        self.assertGreater(self.sup.throttle['a'],0)
        self.assertNotIn('a',self.sup.forced)

    def test_disconnection_cancels_force_and_does_not_rearm_on_reconnect(self):
        self.sup.force_run('a',35)
        self.sup.ready['a'] = False
        self.assertEqual(self.sup.tick()['a'],0)
        self.assertNotIn('a',self.sup.forced)
        self.sup.ready['a'] = True
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],0)
        self.sup.ready['a'] = False
        self.assertEqual(self.sup.force_run('a'),0)
        self.assertNotIn('a',self.sup.forced)

    def test_external_has_no_force_commands_and_timing_alarm_continues_during_force(self):
        self.assertEqual(self.sup.force_run('p',40),0)
        self.assertNotIn('p',self.tracker.commands)
        self.sup.force_run('a',40)
        self.now += 101
        self.sup.tick()
        self.assertTrue(self.sup.alarms)
        self.assertIn('a',self.sup.faults)
        self.assertEqual(self.sup.throttle['a'],40)


class CollisionTests(unittest.TestCase):
    setUp = OverrideTests.setUp
    read = OverrideTests.read

    def enter_behind(self):
        self.read('p','5',0)
        self.sup.request('a',30)
        self.read('p','0',5)

    def test_external_enters_behind_manual_train_continues_with_collision_alarm(self):
        self.enter_behind()
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],30)
        self.assertEqual(self.sup.occupied()['0'],{'a','p'})
        self.assertTrue(self.sup.follower_behind('a','p'))
        self.assertEqual(self.sup.external_reservations()['1'],'a')
        for tid in ('a','p'):
            messages = [message for key,message in self.sup.alarms.items() if key[0] == tid and key[1] == 'collision']
            self.assertTrue(messages)
            self.assertIn('POTENTIAL COLLISION',messages[0])
            self.assertIn('Blue block',messages[0])
        self.assertIn('continuing ahead',self.sup.automatic_status('a'))
        self.assertNotIn('stale',card_speed(self.sup,'p')[0])

    def test_auto_train_continues_and_alarm_clears_when_leader_exits(self):
        self.enter_behind()
        self.sup.enabled = self.sup.automatic['a'] = True
        self.sup.target = .08
        self.sup.tick()
        self.assertGreater(self.sup.throttle['a'],0)
        self.read('a','1',6)
        self.sup.tick()
        self.assertFalse(any(key[1] == 'collision' for key in self.sup.alarms))
        self.assertGreater(self.sup.throttle['a'],0)

    def test_first_external_read_cannot_zero_a_matching_train_already_ahead(self):
        self.tracker.speeds['p'] = .08
        self.sup.set_pace_train('p')
        self.sup.tick()
        old = self.sup.throttle['a']
        self.assertGreater(old,0)
        self.read('p','0',5)  # First timed source read: speed temporarily unknown.
        self.assertNotIn('p',self.tracker.speeds)
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],old)
        self.assertTrue(self.sup.alarms)

    def test_external_ahead_head_on_or_ambiguous_entry_still_stops(self):
        for direction, entered in ((1,99.),(-1,105.),(1,100.)):
            with self.subTest(direction=direction,entered=entered):
                self.setUp()
                self.sup.request('a',30)
                self.tracker.last_seen['p'] = ('0',entered)
                self.tracker.directions['p'] = direction
                self.sup.observe('p')
                self.sup.tick()
                self.assertEqual(self.sup.throttle['a'],0)
                self.assertTrue(self.sup.alarms)

    def test_stopped_train_does_not_start_just_because_external_enters_behind(self):
        self.read('p','0',5)
        self.sup.enabled = self.sup.automatic['a'] = True
        self.sup.target = .08
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],0)
        self.assertFalse(self.sup.follower_behind('a','p'))

    def test_two_controlled_trains_still_stop_and_now_sound_collision_alarm(self):
        self.sup.request('a',30)
        self.sup.request('b',30)
        self.tracker.last_seen['b'] = ('0',self.now)
        self.sup.tick()
        self.assertEqual((self.sup.throttle['a'],self.sup.throttle['b']),(0,0))
        self.assertTrue(any(key[1] == 'collision' for key in self.sup.alarms))

    def test_rear_entry_does_not_override_an_occupied_block_ahead(self):
        self.enter_behind()
        self.tracker.last_seen['b'] = ('1',self.now)
        self.tracker.frozen['a'] = .95
        self.tracker.reset_motion('a')
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],0)

    def test_exception_covers_retained_tail_blocks_and_ends_if_external_gets_ahead(self):
        self.tracker.clearance.enabled.add('a')
        self.read('a','0',0)
        self.tracker.clearance.rear('a','0',{'unix_ms':100001})
        self.read('p','5',0)
        self.sup.request('a',30)
        self.read('a','1',4)
        self.read('p','0',5)
        self.sup.tick()
        self.assertGreater(self.sup.throttle['a'],0)
        self.assertTrue(self.sup.follower_behind('a','p'))
        self.assertEqual(self.sup.occupied()['0'],{'a','p'})
        self.read('p','1',6)
        self.sup.tick()
        self.assertGreater(self.sup.throttle['a'],0)
        self.read('p','2',7)
        self.tracker.frozen['a'] = .95
        self.tracker.reset_motion('a')
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],0)

    def test_stop_cancels_rear_entry_exception(self):
        self.enter_behind()
        self.sup.request('a',0)
        self.assertFalse(self.sup.follower_behind('a','p'))
        self.assertEqual(self.sup.request('a',30),0)
