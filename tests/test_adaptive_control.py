import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from fleet_automation import Supervisor
from fleet_setup import register_tag, save_config
from loop_tracking import LoopTracker
from train_alarm import TrainAlarm


def configuration(rear=False):
    trains = [{'id':tid,'name':tid.upper(),'hub_name':tid,'tags':[uid]}
              for tid,uid in (('a','01020304'),('b','05060708'))]
    if rear:
        trains[0]['rear_tag'] = '11121314'
    return dict(trains=trains, readers=[dict(id=str(i),name=str(i),distance_to_next_m=2.) for i in range(6)])


class AdaptiveTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.config = configuration()
        self.setup_tracker()

    def setup_tracker(self):
        self.tracker = LoopTracker(self.config, clock=lambda:self.now)
        self.sup = Supervisor(self.config,self.tracker)
        self.sup.remove_from_track('b')

    def read(self, reader, seconds, uid='01020304', epoch=None):
        self.now = 100+seconds
        previous = self.tracker.last_seen.get('a',(None,None))[0]
        result = self.tracker.ingest(json.dumps(dict(reader=reader,uid=uid,
                                   unix_ms=int(self.now*1000) if epoch is None else epoch)),reader)
        if result:
            self.sup.observe(result[0],previous,rear=uid in self.tracker.rear_tags)
        return result

    def place(self, reader):
        self.tracker.last_seen['a'] = (reader,self.now)
        self.tracker.blocks['a'] = reader
        self.tracker.reset_motion('a')
        self.tracker.journeys.pop('a',None)
        event = dict(reader=reader,uid='01020304',unix_ms=int(self.now*1000))
        self.tracker.passages['a'] = event
        self.tracker.previous_passage['a'] = (event,self.now)

    def learn_two_sections(self):
        self.read('0',0)
        self.sup.request('a',40)
        self.read('1',10)
        self.read('2',30)

    def test_section_specific_load_and_time_survive_save_and_reload(self):
        self.learn_two_sections()
        straight = self.tracker.learning.get('a','0')
        curve = self.tracker.learning.get('a','1')
        self.assertEqual(straight['seconds'],10)
        self.assertEqual(curve['seconds'],20)
        self.assertAlmostEqual(straight['gain'],.005)
        self.assertAlmostEqual(curve['gain'],.0025)
        with tempfile.TemporaryDirectory() as folder:
            saved = save_config(Path(folder)/'fleet.json',self.config)
        rebuilt = LoopTracker(saved)
        rebuilt.last_seen['a'] = ('1',0)
        self.assertAlmostEqual(rebuilt.gain_for('a'),.0025)
        rebuilt.directions['a'] = -1
        self.assertIsNone(rebuilt.gain_for('a'))  # Reverse needs its own history.
        self.sup.enabled = True
        self.sup.automatic['a'] = True
        self.sup.target = .08
        self.place('0')
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],16)
        self.place('1')
        for _ in range(10):
            self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],32)

    def test_waits_excluded_from_learned_times_and_throttle_model(self):
        self.read('0',0)
        self.sup.request('a',40)
        self.now += 2
        self.sup.request('a',0)
        self.now += 100
        self.sup.tick()
        self.assertFalse(self.sup.alarms)
        self.sup.request('a',20)
        self.read('1',104)
        sample = self.tracker.learning.get('a','0')
        self.assertEqual(sample['seconds'],4)
        self.assertAlmostEqual(sample['gain'],2/120)

    def test_curve_deadline_alarms_at_two_and_stops_at_two_and_half(self):
        self.learn_two_sections()
        self.place('1')
        self.sup.request('a',40)  # Learned curve expectation = 20 seconds.
        self.now += 39.9
        self.sup.tick()
        self.assertFalse(self.sup.alarms)
        self.now += .2
        self.sup.tick()
        self.assertTrue(self.sup.alarms)
        self.assertEqual(self.sup.throttle['a'],40)
        self.assertFalse(self.sup.faults)
        self.now += 10
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],0)
        self.assertIn('a',self.sup.faults)
        self.assertTrue(self.sup.alarms)
        # A genuine next checkpoint clears the incident but doesn't teach the delay.
        samples = self.tracker.learning.get('a','1')['samples']
        self.read('2',self.now-100+1)
        self.sup.tick()
        self.assertFalse(self.sup.alarms)
        self.assertNotIn('a',self.sup.faults)
        self.assertEqual(self.tracker.learning.get('a','1')['samples'],samples)

    def test_commanded_slowdown_extends_deadline_without_resetting_elapsed_work(self):
        self.learn_two_sections()
        self.place('0')
        self.sup.request('a',40)
        self.now += 10
        self.sup.request('a',20)
        self.now += 19
        self.sup.tick()
        self.assertFalse(self.sup.alarms)  # 1 + 19/20 expected journeys.
        self.now += 2
        self.sup.tick()
        self.assertTrue(self.sup.alarms)
        self.assertEqual(self.sup.throttle['a'],20)
        self.sup.request('a',0)
        progress = self.tracker.timing('a')[0]
        self.now += 1000
        self.sup.tick()
        self.assertAlmostEqual(self.tracker.timing('a')[0],progress)
        self.assertNotIn('a',self.sup.faults)

    def test_duplicate_front_read_cannot_reset_deadline(self):
        self.read('0',0)
        self.sup.request('a',40)
        self.read('0',20)
        self.assertEqual(self.tracker.last_seen['a'],('0',100))
        self.now += 61
        self.sup.tick()
        self.assertTrue(self.sup.alarms)

    def test_changed_geometry_does_not_reuse_stale_section_model(self):
        self.learn_two_sections()
        self.config['readers'][0]['distance_to_next_m'] = 3
        rebuilt = LoopTracker(self.config)
        self.assertIsNone(rebuilt.learning.get('a','0'))
        self.assertIsNotNone(rebuilt.learning.get('a','1'))

    def test_adaptive_cruise_slows_for_closing_gap_and_recovers(self):
        self.place('0')
        self.tracker.frozen['a'] = .7
        self.sup.on_track['b'] = self.sup.service['b'] = True
        self.tracker.last_seen['b'] = ('1',self.now)
        self.tracker.speed_per_throttle.update(a=.004,b=.002)
        self.sup.request('b',20)
        target = self.sup.adaptive_target('a',.12)
        self.assertLess(target,.12)
        self.assertIn('B',self.sup.cruise_reasons['a'])
        self.tracker.last_seen['b'] = ('3',self.now)
        self.assertEqual(self.sup.adaptive_target('a',.12),.12)


class RearTagTests(unittest.TestCase):
    setup_tracker = AdaptiveTests.setup_tracker
    read = AdaptiveTests.read
    def setUp(self):
        self.now = 100.
        self.config = configuration(True)
        self.setup_tracker()

    def test_front_then_rear_controls_clearance_without_corrupting_speed(self):
        self.read('0',0)
        self.sup.request('a',40)
        self.assertEqual(set(self.sup.occupied()),{'5','0'})
        self.read('0',1,'11121314')
        self.assertEqual(set(self.sup.occupied()),{'0'})
        self.read('1',10)
        self.assertEqual(set(self.sup.occupied()),{'0','1'})
        seen = self.tracker.last_seen['a']
        self.read('1',12,'11121314')
        self.assertEqual(self.tracker.last_seen['a'],seen)
        self.assertAlmostEqual(self.tracker.speeds['a'],.2)
        self.assertEqual(set(self.sup.occupied()),{'1'})

    def test_train_can_span_multiple_blocks_and_keeps_missing_rear_holds(self):
        self.read('0',0)
        self.read('1',10)
        self.read('2',20)
        self.assertEqual(set(self.sup.occupied()),{'5','0','1','2'})
        self.read('2',21,'11121314')  # Earlier rear passages were missed.
        self.assertEqual(set(self.sup.occupied()),{'5','0','2'})
        self.sup.remove_from_track('a')
        self.assertFalse(self.sup.occupied())

    def test_long_initial_train_retains_enough_approach_blocks(self):
        self.config['trains'][0]['length_m'] = 5
        self.read('0',0)
        self.assertEqual(set(self.sup.occupied()),{'3','4','5','0'})
        self.read('0',1,'11121314')
        self.assertEqual(set(self.sup.occupied()),{'0'})

    def test_early_stale_and_duplicate_rear_reads_cannot_unlock(self):
        self.read('0',0,'11121314')
        self.read('0',2)
        self.read('0',3,'11121314',epoch=101000)
        self.assertIn('5',self.sup.occupied())
        self.read('0',4,'11121314')
        self.assertNotIn('5',self.sup.occupied())
        self.read('1',10)
        self.read('0',11,'11121314')
        self.assertIn('0',self.sup.occupied())

    def test_rear_timeout_alarm_survives_fresh_front_reads_and_blocks_followers(self):
        self.read('0',0)
        self.sup.request('a',40)
        for reader,seconds in (('1',30),('2',60),('3',81)):
            self.read(reader,seconds)
            self.sup.tick()
        self.assertTrue(any(key[1] == 'rear' for key in self.sup.alarms))
        self.assertGreater(self.sup.throttle['a'],0)
        self.now = 201
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],0)
        self.assertIn('Rear',self.sup.faults['a'])
        self.assertIn('0',self.sup.occupied())

    def test_reverse_rejected_with_rear_clearance_without_releasing_blocks(self):
        self.read('0',0)
        before = self.sup.occupied()
        self.assertEqual(self.sup.request('a',-30),0)
        self.assertIn('forward',self.sup.reasons['a'])
        self.assertEqual(self.sup.occupied(),before)

    def test_follower_waits_until_rear_clears_and_auto_resumes(self):
        self.read('1',0)
        self.read('1',1,'11121314')
        self.read('2',10)
        self.sup.on_track['b'] = self.sup.service['b'] = True
        self.tracker.last_seen['b'] = ('0',self.now)
        self.sup.enabled = self.sup.automatic['b'] = True
        self.sup.target = .08
        self.sup.tick()
        self.assertEqual(self.sup.throttle['b'],0)
        self.assertTrue(self.sup.waiting['b'])
        self.assertIn('Orange block',self.sup.reasons['b'])
        self.read('2',12,'11121314')
        self.sup.tick()
        self.assertGreater(self.sup.throttle['b'],0)
        self.assertTrue(self.sup.automatic['b'])

    def test_two_trains_with_curves_and_rear_clearance_keep_separation(self):
        self.sup.on_track['b'] = self.sup.service['b'] = True
        physical = {'a':0.,'b':6.}
        lengths = {'a':.5,'b':.5}
        self.read('0',0)
        self.tracker.last_seen['b'] = ('3',self.now)
        self.sup.enabled = True
        self.sup.automatic.update(a=True,b=True)
        self.sup.target = .08
        for step in range(4500):
            self.sup.tick()
            self.now += .1
            for tid,uid in (('a','01020304'),('b','05060708')):
                old = physical[tid]
                block = int(old/2)%6
                gain = (.002 if block in (1,4) else .004) * (1.2 if tid == 'b' else 1.)
                physical[tid] += self.sup.throttle[tid]*gain*.1
                new_block = int(physical[tid]/2)%6
                if new_block != block:
                    self.tracker.ingest(json.dumps(dict(reader=str(new_block),uid=uid,unix_ms=round(self.now*1000))),str(new_block))
                    self.sup.observe(tid,str(block))
                if tid == 'a':
                    old_rear, new_rear = int((old-lengths[tid])//2)%6, int((physical[tid]-lengths[tid])//2)%6
                    if old_rear != new_rear:
                        self.tracker.ingest(json.dumps(dict(reader=str(new_rear),uid='11121314',unix_ms=round(self.now*1000))),str(new_rear))
                        self.sup.observe(tid,rear=True)
            self.assertNotEqual(int(physical['a']/2)%6,int(physical['b']/2)%6,step)
            gap = (physical['b']-physical['a']) % 12
            self.assertGreaterEqual(gap,lengths['b'],step)
            self.assertGreaterEqual(12-gap,lengths['a'],step)
        self.assertGreater(physical['a'],12)
        self.assertFalse(self.sup.faults)
        self.assertGreater(len(self.tracker.learning.samples),6)


class AlarmAndConfigTests(unittest.TestCase):
    def test_alarm_repeats_silences_current_incidents_and_sounds_for_new_ones(self):
        now = [0.]
        bell = Mock()
        alarm = TrainAlarm(bell,lambda:now[0])
        alarm.update({'a':'overdue'})
        self.assertEqual(bell.call_count,1)
        now[0] = 2
        alarm.update({'a':'overdue'})
        self.assertEqual(bell.call_count,1)
        now[0] = 3
        alarm.update({'a':'overdue'})
        self.assertEqual(bell.call_count,2)
        alarm.silence()
        now[0] = 6
        alarm.update({'a':'overdue'})
        self.assertEqual(bell.call_count,2)
        alarm.update({'a':'overdue','b':'new'})
        self.assertEqual(bell.call_count,3)
        alarm.update({})
        alarm.update({'a':'again'})
        self.assertEqual(bell.call_count,4)

    def test_rear_registration_roles_and_duplicate_config_validation(self):
        config = configuration()
        register_tag(config,'a','11:12:13:14',role='rear')
        self.assertEqual(config['trains'][0]['rear_tag'],'11121314')
        with self.assertRaises(ValueError):
            register_tag(config,'b','11121314')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'fleet.json'
            saved = save_config(path,config)
            self.assertEqual(saved['trains'][0]['rear_tag'],'11121314')
            for modify in (lambda c:c['trains'][0].update(rear_tag='01020304'),
                           lambda c:c.update(automation={'stop_time_factor':2}),
                           lambda c:c['trains'][0].update(section_history=[{'seconds':float('nan') }])):
                bad = copy.deepcopy(config)
                modify(bad)
                with self.assertRaises(ValueError):
                    save_config(path,bad)

