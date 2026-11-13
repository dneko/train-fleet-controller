import asyncio
import json
import queue
import tempfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock

from fleet_automation import Supervisor
from fleet_controller import Backend, TrainConnection
from fleet_model import load_config
from loop_tracking import LoopTracker


def configuration():
    return {
        'trains': [
            {'id':'a','name':'A','hub_name':'A','tags':[]},
            {'id':'b','name':'B','hub_name':'B','tags':[]},
            {'id':'p','name':'Pace train','control_mode':'external','tags':['01020304']},
        ],
        'readers': [{'id':str(i),'name':str(i),'distance_to_next_m':2.,'x':100+i*60,'y':100}
                    for i in range(6)],
    }


class ExternalControlTests(unittest.TestCase):
    def setUp(self):
        self.now=100.
        self.config=configuration()
        self.tracker=LoopTracker(self.config,clock=lambda:self.now)
        self.sup=Supervisor(self.config,self.tracker)
        self.tracker.last_seen={'a':('0',self.now),'b':('2',self.now),'p':('4',self.now)}
        self.tracker.speed_per_throttle.update(a=.004,b=.002)
        self.sup.ready['p']=False

    def start_matching(self, speed=.08):
        self.tracker.speeds['p']=speed
        self.sup.set_pace_train('p')

    def test_external_train_never_gets_a_command_or_needs_hub_readiness(self):
        self.start_matching()
        self.assertEqual(self.sup.request('p',40),0)
        self.assertEqual(self.sup.request('p',0),0)
        self.assertNotIn('p',self.tracker.commands)
        commands=self.sup.tick()
        self.assertNotIn('p',commands)
        self.assertGreater(commands['a'],0)
        self.assertFalse(self.sup.automatic['p'])

    def test_followers_are_forced_auto_and_match_each_load_with_speed_cap(self):
        self.start_matching()
        for _ in range(10):
            self.sup.automatic['a']=False
            self.sup.tick()
        self.assertTrue(self.sup.automatic['a'])
        self.assertTrue(self.sup.automatic['b'])
        self.assertEqual((self.sup.throttle['a'],self.sup.throttle['b']),(20,40))
        self.tracker.speeds['p']=.04
        self.sup.tick()
        self.assertEqual((self.sup.throttle['a'],self.sup.throttle['b']),(10,20))
        self.tracker.speeds['p']=.5
        self.sup.max_speed=.03
        self.sup.tick()
        self.assertLessEqual(self.tracker.estimated_speed('a'),.03)
        self.assertLessEqual(self.tracker.estimated_speed('b'),.03)

    def test_missing_stale_reversed_and_off_track_source_hold_followers(self):
        self.sup.set_pace_train('p')
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],0)
        self.assertIn('two consecutive',self.sup.automatic_status('a'))
        self.tracker.speeds['p']=.08
        self.sup.tick()
        self.assertGreater(self.sup.throttle['a'],0)
        self.now+=65  # Power-down hold now follows the 2.5× timing limit.
        # Keep the followers' own detections fresh to isolate source freshness.
        self.tracker.last_seen.update(a=('0',self.now),b=('2',self.now))
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],0)
        self.assertTrue(self.sup.automatic['a'])
        self.assertIn('fresh checkpoint',self.sup.automatic_status('a'))
        self.tracker.last_seen['p']=('4',self.now)
        self.sup.observe('p')
        self.sup.tick()
        self.assertGreater(self.sup.throttle['a'],0)
        self.tracker.directions['p']=-1
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],0)
        self.assertIn('forward-only',self.sup.automatic_status('a'))
        self.sup.remove_from_track('p')
        self.assertIn('off track',self.sup.automatic_status('a'))

    def test_external_occupancy_and_next_route_still_protect_followers(self):
        self.tracker.last_seen['p']=('1',self.now)
        self.tracker.frozen['a']=.9
        self.start_matching()
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],0)
        self.assertTrue(self.sup.waiting['a'])
        self.assertIn('Pace train',self.sup.reasons['a'])
        # B already occupies the external train's next block: let it leave.
        self.assertGreater(self.sup.throttle['b'],0)
        self.tracker.last_seen['p']=('4',self.now)
        self.tracker.frozen['a']=.1  # Near the reverse boundary into block 5.
        self.tracker.reset_motion('a')
        self.assertEqual(self.sup.request('a',-30),0)  # External route reserves block 5.
        self.assertIn('reserved',self.sup.reasons['a'])
        self.assertEqual(self.sup.occupied()['4'],{'p'})

    def test_individual_stop_remains_stopped_until_resume_in_matching_mode(self):
        self.start_matching()
        self.sup.tick()
        self.sup.pause('a')
        self.sup.set_pace_train('p')  # Reselecting a source must not undo Stop.
        for _ in range(5):
            self.sup.tick()
            self.assertEqual(self.sup.throttle['a'],0)
            self.assertTrue(self.sup.automatic['a'])
        self.assertTrue(self.sup.can_resume('a'))
        self.sup.resume('a')
        self.sup.tick()
        self.assertGreater(self.sup.throttle['a'],0)

    def test_invalid_source_cannot_enable_matching(self):
        with self.assertRaises(ValueError):
            self.sup.set_pace_train('a')
        with self.assertRaises(ValueError):
            self.sup.set_pace_train('missing')

    def read_source(self, reader, elapsed):
        self.now=100+elapsed
        return self.tracker.ingest(json.dumps({'reader':reader,'uid':'01020304',
                                   'unix_ms':int(self.now*1000)}),reader)

    def test_external_speed_is_measured_from_reads_without_motor_commands(self):
        self.read_source('0',0)
        self.read_source('1',10)
        self.assertAlmostEqual(self.tracker.speeds['p'],.2)
        self.tracker.set_command('p',0)
        self.assertNotIn('p',self.tracker.commands)
        self.now+=5
        self.assertAlmostEqual(self.tracker.position('p')['fraction'],.5)

    def test_external_reverse_detection_uses_the_correct_block(self):
        self.read_source('0',0)
        self.read_source('5',10)
        self.assertEqual(self.tracker.directions['p'],-1)
        self.assertEqual(self.tracker.block_for('p'),'4')
        self.assertAlmostEqual(self.tracker.speeds['p'],.2)

    def test_checkpoint_measurements_drive_followers_and_invalid_sample_holds(self):
        self.sup.set_pace_train('p')
        self.read_source('3',0)
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],0)
        self.read_source('4',25)  # 2 m / 25 s = 10 studs/s.
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],20)
        self.read_source('5',75)  # Slower next interval: 5 studs/s.
        self.tracker.last_seen.update(a=('0',self.now),b=('2',self.now))
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],10)
        self.assertEqual(self.sup.throttle['b'],20)
        self.read_source('2',80)  # Skipped checkpoints invalidate the speed.
        self.sup.tick()
        self.assertEqual(self.sup.throttle['a'],0)
        self.assertNotIn('p',self.tracker.commands)


class ExternalConfigurationTests(unittest.TestCase):
    def load(self, config):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'fleet.json'
            path.write_text(json.dumps(config))
            return load_config(path)

    def test_external_train_does_not_require_a_bluetooth_hub_name(self):
        config=configuration()
        config['automation']={'pace_train_id':'p'}
        self.assertEqual(self.load(config)['trains'][2]['control_mode'],'external')
        config['trains'][2]['control_mode']='fleet'
        with self.assertRaises(ValueError):
            self.load(config)

    def test_only_external_ids_can_be_persisted_as_speed_sources(self):
        for source in ('a','missing',False):
            config=configuration()
            config['automation']={'pace_train_id':source}
            with self.assertRaises(ValueError):
                self.load(config)

    def test_external_trains_have_no_backend_connections_or_queued_commands(self):
        backend=Backend.__new__(Backend)
        backend.events=queue.Queue()
        backend.loop=MagicMock()
        backend.demo=False
        backend.trains=backend.connections(configuration())
        self.assertEqual(set(backend.trains),{'a','b'})
        backend.connect('p')
        backend.speed('p',40)
        backend.speed('p',0)
        backend.waiting('p',True)
        backend.loop.call_soon_threadsafe.assert_not_called()
        direct=TrainConnection(configuration()['trains'][2],lambda _:None)
        direct.accepting=True
        direct.set_speed(40)
        direct.set_waiting(True)
        self.assertIsNone(direct.pending)
        asyncio.run(direct.run())  # Returns before importing/scanning Bluetooth.
