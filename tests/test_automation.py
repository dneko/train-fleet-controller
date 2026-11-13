import json
import unittest
from fleet_automation import Supervisor
from loop_tracking import LoopTracker
from map_geometry import interpolate, nearest_leg


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.now=10
        self.config={'trains':[{'id':t,'name':t,'tags':[],'start_throttle':25,'max_throttle':70} for t in ('a','b')],
                     'readers':[{'id':str(i),'name':str(i),'distance_to_next_m':2} for i in range(6)]}
        self.tracker=LoopTracker(self.config,clock=lambda:self.now)
        self.sup=Supervisor(self.config,self.tracker)
        self.tracker.last_seen={'a':('0',10),'b':('3',10)}

    def test_single_sensor_interval_occupied_and_next_reserved(self):
        self.assertEqual(self.sup.request('a',40),40)
        self.assertEqual(self.sup.reservations['1'],'a')
        self.assertEqual(self.sup.occupied()['0'],{'a'})
        self.assertNotIn('5',self.sup.occupied())
        self.tracker.last_seen['b']=('1',10)
        self.assertEqual(self.sup.request('a',40),0)

    def test_unknown_train_holds_auto_but_manual_can_locate_it(self):
        del self.tracker.last_seen['b']
        self.assertEqual(self.sup.request('b',30),30)
        self.assertEqual(self.sup.request('a',40),40)
        self.sup.automatic['a'] = True
        self.assertEqual(self.sup.request('a',40),0)
        self.sup.remove_from_track('b')
        self.assertEqual(self.sup.request('a',40),40)

    def test_manual_hold_reason_survives_refresh_until_retry_or_stop(self):
        self.sup.ready['a'] = False
        self.assertEqual(self.sup.request('a',40),0)
        for _ in range(5):
            self.assertEqual(self.sup.tick(), {})
            self.assertIn('Hub not ready', self.sup.automatic_status('a'))
        self.sup.ready['a'] = True
        # Clearing the obstruction must not restart a refused manual command.
        self.assertEqual(self.sup.tick(), {})
        self.assertEqual(self.sup.throttle['a'], 0)
        self.assertEqual(self.sup.request('a',40),40)
        self.assertEqual(self.sup.automatic_status('a'), '')
        self.sup.ready['a'] = False
        self.assertEqual(self.sup.tick()['a'], 0)
        self.sup.tick()
        self.assertEqual(self.sup.automatic_status('a'), 'Hub not ready')
        self.sup.request('a', 0)
        self.assertEqual(self.sup.automatic_status('a'), '')

    def test_out_of_service_keeps_occupancy(self):
        self.sup.service['b']=False
        self.assertIn('b',self.sup.occupied()['3'])
        self.assertEqual(self.sup.request('b',40),0)
        self.sup.remove_from_track('b')
        self.assertNotIn('3',self.sup.occupied())

    def test_moving_reads_restore_service(self):
        self.sup.service['a']=False
        self.sup.observe('a',None)
        self.assertFalse(self.sup.service['a'])
        self.tracker.last_seen['a']=('1',10)
        self.sup.observe('a','0')
        self.assertTrue(self.sup.service['a'])

    def test_load_model_controls_target_and_zero(self):
        self.sup.enabled=True
        self.sup.automatic['a']=True
        self.sup.target=.08
        self.tracker.speed_per_throttle['a']=.004
        self.assertEqual(self.sup.tick()['a'],20)
        self.assertNotIn('a',self.sup.tick())
        self.sup.target=.04
        self.assertEqual(self.sup.tick()['a'],10)
        self.sup.target=0
        self.assertEqual(self.sup.tick()['a'],0)

    def test_missing_reader_latches_global_hold(self):
        self.sup.request('a',40)
        self.now=111  # 2.5 × the conservative 40-second startup allowance.
        self.sup.tick()
        self.assertIn('a',self.sup.faults)
        self.assertEqual(self.sup.request('b',40),0)

    def test_margin_unassigned_tag_and_disconnected_guards(self):
        self.config['trains'][0]['length_m']=3
        self.assertEqual(self.sup.request('a',40),40)  # No extra block for the tail.
        self.config['trains'][0]['stop_margin_m']=3
        self.assertEqual(self.sup.request('a',40),0)
        self.config['trains'][0]['stop_margin_m']=.3
        self.sup.observe(None)
        self.assertEqual(self.sup.request('a',40),40)
        self.sup.ready['a']=False
        self.assertEqual(self.sup.request('a',40),0)

    def test_polyline_distance_interpolation_and_insertion(self):
        path=[(0,0),(100,0),(100,200)]
        self.assertEqual(interpolate(path,.5),(100,50))
        self.assertEqual(nearest_leg(path,90,150)[1],1)
        self.assertEqual(interpolate(path,2),(100,200))


    def test_reenabling_removed_train_requires_localization(self):
        self.sup.remove_from_track('a')
        self.sup.service['a']=True
        self.assertEqual(self.sup.request('a',40),0)


    def test_own_tail_and_reservation_do_not_block_train(self):
        self.sup.remove_from_track('b')
        for index in range(12):
            self.tracker.last_seen['a']=(str(index % 6),self.now)
            self.sup.tick()
            self.assertEqual(self.sup.request('a',40),40)
            self.now+=1

    def test_long_stationary_wait_does_not_fault_on_restart(self):
        self.sup.remove_from_track('b')
        self.sup.request('a',0)
        self.now=1000
        self.assertEqual(self.sup.request('a',40),40)
        self.sup.tick()
        self.assertNotIn('a',self.sup.faults)
        self.now+=100
        self.sup.tick()
        self.assertIn('a',self.sup.faults)

    def test_hold_reason_names_other_train(self):
        self.tracker.last_seen['b']=('1',10)
        self.sup.request('a',40)
        self.assertIn('Waiting for b', self.sup.reasons['a'])


    def test_train_two_blocks_ahead_does_not_block(self):
        self.tracker.last_seen['b']=('2',10)
        self.assertEqual(self.sup.request('a',40),40)

    def test_passage_releases_only_old_sensor_interval(self):
        self.assertIn('0',self.sup.occupied())
        self.tracker.last_seen['a']=('1',11)
        self.sup.observe('a','0')
        self.assertNotIn('0',self.sup.occupied())
        self.assertEqual(self.sup.occupied()['1'],{'a'})

    def test_reverse_reserves_previous_block_and_respects_occupancy(self):
        self.assertEqual(self.sup.request('a',-100),-70)
        self.assertEqual(self.sup.reservations['5'],'a')
        self.tracker.set_command('a',-70)
        self.tracker.last_seen['b']=('5',10)
        self.assertEqual(self.sup.request('a',-40),0)
        self.assertIn('Waiting for b',self.sup.reasons['a'])

    def test_reverse_timeout_and_stop(self):
        self.sup.remove_from_track('b')
        self.assertEqual(self.sup.request('a',-40),-40)
        self.tracker.set_command('a',-40)
        self.now=111
        commands=self.sup.tick()
        self.assertEqual(commands['a'],0)
        self.assertIn('a',self.sup.faults)

    def test_reverse_detected_block_used_for_occupancy(self):
        self.tracker.blocks['a']='5'
        self.tracker.directions['a']=-1
        self.assertEqual(self.sup.occupied()['5'],{'a'})
        self.assertNotIn('0',self.sup.occupied())
        self.assertEqual(self.sup.request('a',-40),-40)
        self.assertEqual(self.sup.reservations['4'],'a')

    def test_auto_stop_explanations_are_current_and_read_only(self):
        self.sup.automatic['a']=True
        self.assertIn('enable Auto fleet',self.sup.automatic_status('a'))
        self.sup.enabled=True
        self.assertIn('target speed is zero',self.sup.automatic_status('a'))
        self.sup.target=.3
        self.sup.ready['a']=False
        self.assertIn('Hub not ready',self.sup.automatic_status('a'))
        self.sup.ready['a']=True
        self.sup.service['a']=False
        self.assertIn('Out of service',self.sup.automatic_status('a'))
        self.sup.service['a']=True
        self.tracker.last_seen['b']=('1',10)
        self.assertIn('Waiting for b',self.sup.automatic_status('a'))
        self.tracker.last_seen['b']=('3',10)
        before=dict(self.sup.reservations)
        self.assertIn('Auto ready',self.sup.automatic_status('a'))
        self.assertEqual(self.sup.reservations,before)
        self.sup.request('a',25)
        self.assertEqual(self.sup.automatic_status('a'),'Auto running')

    def test_calibrated_train_approaches_occupied_next_block_then_holds(self):
        self.tracker.last_seen['b']=('1',10)
        self.tracker.speed_per_throttle['a']=.004
        self.sup.target=.12
        self.sup.enabled=True
        self.sup.automatic['a']=True
        self.assertGreater(self.sup.tick()['a'],0)
        seen = []
        for _ in range(400):
            self.now += .1
            self.sup.tick()
            seen.append(self.sup.throttle['a'])
            self.assertEqual(self.tracker.block_for('a'),'0')
            self.assertEqual(self.sup.occupied()['1'],{'b'})
            if self.sup.waiting['a']:
                break
        self.assertTrue(self.sup.waiting['a'])
        self.assertTrue(any(0 < value < 25 for value in seen))
        fraction = self.tracker.position('a')['fraction']
        self.assertGreater(fraction,.3)  # Adaptive following leaves room for the leading train's length.
        self.assertLess(fraction,.85)  # Stops before the 0.3 m margin.
        self.assertIn('Orange block',self.sup.reasons['a'])
        self.now += 120
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],0)
        self.assertEqual(self.tracker.position('a')['fraction'],fraction)
        self.assertNotIn('a',self.sup.faults)
        self.tracker.last_seen['b']=('2',self.now)
        self.assertGreater(self.sup.tick()['a'],0)
        self.assertFalse(self.sup.waiting['a'])

    def test_adjacent_trains_can_both_move_in_separate_blocks(self):
        self.tracker.last_seen['b']=('1',10)
        self.tracker.speed_per_throttle.update(a=.004,b=.006)
        self.assertEqual(self.sup.request('a',20),20)
        self.assertEqual(self.sup.request('b',20),20)
        self.assertEqual(self.sup.occupied(),{'0':{'a'},'1':{'b'}})
        self.assertEqual(self.sup.reservations,{})  # No whole-block gap.

    def test_same_block_conflict_stops_both_trains(self):
        self.sup.request('a',30)
        self.sup.request('b',30)
        self.tracker.last_seen['b']=('0',10)
        self.assertEqual(self.sup.tick(),{'a':0,'b':0})
        self.assertEqual(self.sup.request('a',20),0)
        self.assertEqual(self.sup.request('b',20),0)

    def test_two_approaches_cannot_reserve_same_empty_block(self):
        self.tracker.last_seen['b']=('2',10)
        self.tracker.frozen.update(a=.8,b=.2)
        self.tracker.speed_per_throttle.update(a=.004,b=.004)
        self.assertGreater(self.sup.request('a',20),0)
        self.assertEqual(self.sup.reservations['1'],'a')
        # b is reversing toward block 1 from the opposite end.
        self.sup.request('b',-20)
        self.assertEqual(self.sup.reservations['1'],'a')
        self.assertIn('reserved',self.sup.reasons['b'])

    def test_reverse_approach_brakes_before_occupied_block(self):
        self.tracker.last_seen['b']=('5',10)
        self.tracker.frozen['a']=.5
        self.tracker.directions['a']=-1
        self.tracker.speed_per_throttle['a']=.01
        self.assertTrue(-40 < self.sup.request('a',-40) < 0)
        for _ in range(200):
            self.now += .1
            self.sup.tick()
            if self.sup.waiting['a']:
                break
        self.assertTrue(self.sup.waiting['a'])
        self.assertGreater(self.tracker.position('a')['fraction'],.15)
        self.assertEqual(self.tracker.block_for('a'),'0')

    def test_each_load_gets_its_own_throttle_with_a_common_speed_limit(self):
        self.tracker.speed_per_throttle.update(a=.004,b=.002)
        self.sup.enabled=True
        self.sup.automatic.update(a=True,b=True)
        self.sup.max_speed=.08
        self.sup.target=.5  # The configured maximum still wins.
        for _ in range(20):
            self.sup.tick()
        self.assertEqual(self.sup.throttle,{'a':20,'b':40})
        for tid in ('a','b'):
            self.assertLessEqual(self.tracker.estimated_speed(tid),.08)
        self.sup.max_speed=.04
        self.sup.tick()
        self.assertEqual(self.sup.throttle,{'a':10,'b':20})

    def test_uncalibrated_train_waits_when_next_block_is_occupied(self):
        self.tracker.last_seen['b']=('1',10)
        self.assertEqual(self.sup.request('a',30),0)
        self.assertTrue(self.sup.waiting['a'])

    def test_manual_block_hold_can_resume_prior_throttle_only_after_clear(self):
        self.sup.request('a',40)
        self.tracker.last_seen['b']=('1',10)
        self.assertEqual(self.sup.tick()['a'],0)
        self.assertEqual(self.sup.held_throttle['a'],40)
        self.assertFalse(self.sup.can_resume('a'))
        self.assertEqual(self.sup.resume('a'),0)
        self.tracker.last_seen['b']=('2',10)
        self.assertEqual(self.sup.tick(),{})
        self.assertTrue(self.sup.can_resume('a'))
        self.assertIn('Resume +40%',self.sup.automatic_status('a'))
        self.assertEqual(self.sup.resume('a'),40)
        self.assertFalse(self.sup.waiting['a'])
        self.assertNotIn('a',self.sup.held_throttle)

    def test_resume_rechecks_block_and_hub_readiness(self):
        self.tracker.last_seen['b']=('1',10)
        self.sup.request('a',40)
        self.tracker.last_seen['b']=('2',10)
        self.assertTrue(self.sup.can_resume('a'))
        self.tracker.last_seen['b']=('1',10)
        self.assertEqual(self.sup.resume('a'),0)
        self.tracker.last_seen['b']=('2',10)
        self.sup.ready['a']=False
        self.assertEqual(self.sup.resume('a'),0)

    def test_stop_cancels_saved_resume_throttle(self):
        self.tracker.last_seen['b']=('1',10)
        self.sup.request('a',40)
        self.sup.request('a',0)
        self.tracker.last_seen['b']=('2',10)
        self.assertFalse(self.sup.can_resume('a'))
        self.assertEqual(self.sup.resume('a'),0)
        self.assertNotIn('a',self.sup.held_throttle)

    def test_reverse_hold_restores_signed_throttle(self):
        self.tracker.last_seen['b']=('5',10)
        self.sup.request('a',-40)
        self.tracker.last_seen['b']=('3',10)
        self.assertTrue(self.sup.can_resume('a'))
        self.assertEqual(self.sup.resume('a'),-40)

    def test_high_target_does_not_shorten_uncalibrated_checkpoint_deadline(self):
        self.sup.target=.8
        self.sup.request('a',25)
        self.now+=20
        self.sup.tick()
        self.assertNotIn('a',self.sup.faults)
        self.now+=60
        self.sup.tick()
        self.assertNotIn('a',self.sup.faults)  # Alarm at 2×, with power-down grace to 2.5×.
        self.assertTrue(self.sup.alarms)
        self.now+=21
        self.sup.tick()
        self.assertIn('a',self.sup.faults)

    def test_slow_measured_speed_is_not_replaced_by_a_faster_timeout_speed(self):
        self.sup.target=.8
        self.tracker.speed_per_throttle['a']=.001
        self.sup.request('a',20)
        self.now+=80
        self.sup.tick()
        self.assertNotIn('a',self.sup.faults)

    def test_two_loads_follow_stop_and_resume_without_sharing_a_block(self):
        self.tracker.last_seen['b']=('1',10)
        self.tracker.tags={'01020304':'a','05060708':'b'}
        gains={'a':.004,'b':.006}
        self.tracker.speed_per_throttle.update(gains)
        physical={'a':0.,'b':2.}
        tags={'a':'01020304','b':'05060708'}
        self.sup.enabled=True
        self.sup.automatic.update(a=True,b=True)
        self.sup.target=.12
        waited=False
        resumed=False
        for step in range(2000):
            if step == 200:
                self.sup.automatic['b']=False
                self.sup.request('b',0)
            if step == 700:
                self.sup.automatic['b']=True
            self.sup.tick()
            if self.sup.waiting['a']:
                waited=True
            if step > 700 and waited and self.sup.throttle['a'] > 0:
                resumed=True
            self.now += .1
            for tid in ('a','b'):
                old_block=int(physical[tid]/2)%6
                physical[tid] += self.sup.throttle[tid]*gains[tid]*.1
                block=int(physical[tid]/2)%6
                if block != old_block:
                    event={'reader':str(block),'uid':tags[tid], 'boot':'simulation',
                           'seq':step,'unix_ms':round(self.now*1000)}
                    self.tracker.ingest(json.dumps(event),str(block))
                    self.sup.observe(tid,str(old_block))
            self.assertNotEqual(int(physical['a']/2)%6,int(physical['b']/2)%6,step)
        self.assertTrue(waited)
        self.assertTrue(resumed)
        self.assertFalse(self.sup.faults)
