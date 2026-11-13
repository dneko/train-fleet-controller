import json
import unittest

from fleet_automation import Supervisor
from loop_tracking import LoopTracker
from test_adaptive_control import configuration
from train_speed import card_speed


class ContinuousControlTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.config = configuration()
        self.tracker = LoopTracker(self.config,clock=lambda:self.now)
        self.sup = Supervisor(self.config,self.tracker)
        self.tracker.last_seen = {'a':('0',self.now),'b':('3',self.now)}
        self.tracker.speed_per_throttle.update(a=.004,b=.002)
        self.sup.enabled = True
        self.sup.automatic['a'] = True
        self.sup.target = .08

    def test_speed_target_changes_are_sent_between_reads_without_zero_command(self):
        self.assertEqual(self.sup.tick()['a'],20)
        self.now += .1
        self.sup.target = .04
        self.assertEqual(self.sup.tick()['a'],10)
        self.sup.target = .08
        commands = []
        for _ in range(5):
            self.now += .1
            commands.extend(self.sup.tick().values())
        self.assertEqual(self.sup.throttle['a'],20)
        self.assertTrue(all(value > 0 for value in commands))
        self.assertEqual(self.tracker.last_seen['a'],('0',100.))

    def test_preview_adjusts_before_checkpoint_and_crossing_does_not_stop(self):
        self.sup.remove_from_track('b')
        self.tracker.learning.record('a','0',1,20,500)  # .004 m/s per throttle.
        self.tracker.learning.record('a','1',1,10,250)  # .008 in the next section.
        self.tracker.frozen['a'] = .8
        self.sup.request('a',20)
        self.assertEqual(self.sup.tick(),{})
        seen = []
        for _ in range(20):
            self.now += .1
            seen.extend(self.sup.tick().values())
        self.assertLess(self.sup.throttle['a'],20)
        self.assertEqual(self.tracker.block_for('a'),'0')
        self.tracker.ingest(json.dumps(dict(reader='1',uid='01020304',unix_ms=int(self.now*1000))),'1')
        self.sup.observe('a','0')
        seen.extend(self.sup.tick().values())
        self.assertEqual(self.tracker.block_for('a'),'1')
        self.assertEqual(self.sup.throttle['a'],10)
        self.assertTrue(seen)
        self.assertTrue(all(value > 0 for value in seen))

    def test_moving_leader_short_headway_slows_follower_without_cutting_power(self):
        self.tracker.last_seen['b'] = ('1',self.now)
        self.tracker.frozen['a'] = .3
        self.sup.request('b',20)
        self.sup.target = .3
        self.sup.max_speed = .4
        self.sup.tick()
        self.assertGreater(self.sup.throttle['a'],0)
        self.assertLessEqual(self.tracker.estimated_speed('a'),self.tracker.estimated_speed('b'))
        self.assertFalse(self.sup.waiting['a'])
        self.assertIn('following',self.sup.cruise_reasons['a'])
        self.assertIn('B',self.sup.automatic_status('a'))

    def test_collision_hold_still_stops_before_occupied_boundary(self):
        self.tracker.last_seen['b'] = ('1',self.now)
        self.tracker.frozen['a'] = .95
        self.sup.request('b',20)
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],0)
        self.assertTrue(self.sup.waiting['a'])
        self.assertEqual(self.sup.occupied()['1'],{'b'})

    def test_card_speed_tracks_commands_between_reads_and_labels_unknown(self):
        self.sup.tick()
        self.assertIn('10.0 studs/s · est.',card_speed(self.sup,'a')[0])
        self.sup.request('a',10)
        self.assertIn('5.0 studs/s · est.',card_speed(self.sup,'a')[0])
        self.sup.request('a',0)
        self.assertIn('0.0 studs/s · stopped',card_speed(self.sup,'a')[0])
        self.tracker.speed_per_throttle.pop('a')
        self.sup.request('a',20)
        self.assertIn('learning',card_speed(self.sup,'a')[0])

    def test_external_card_has_real_read_average_section_estimate_and_stale_state(self):
        self.config['trains'][0]['control_mode'] = 'external'
        self.tracker = LoopTracker(self.config,clock=lambda:self.now)
        self.sup = Supervisor(self.config,self.tracker)
        self.assertIn('no reads',card_speed(self.sup,'a')[0])
        self.tracker.ingest(json.dumps(dict(reader='0',uid='01020304',unix_ms=100000)),'0')
        self.assertIn('learning',card_speed(self.sup,'a')[0])
        self.now = 125
        self.tracker.ingest(json.dumps(dict(reader='1',uid='01020304',unix_ms=125000)),'1')
        self.assertEqual(card_speed(self.sup,'a')[0],'10.0 studs/s · avg.')
        self.tracker.learning.record('a','1',1,50,0)
        self.assertEqual(card_speed(self.sup,'a')[0],'5.0 studs/s · est.')
        self.sup.faults['a'] = 'Missed checkpoint'
        text,details = card_speed(self.sup,'a')
        self.assertIn('stale',text)
        self.assertIn('no longer reliable',details)
        self.assertNotIn('a',self.tracker.commands)
