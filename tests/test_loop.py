import json
from pathlib import Path
import tempfile
import time
import unittest
from fleet_setup import save_config
from loop_tracking import LoopTracker, motor_speed


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.clock = 10
        self.epoch = int(time.time()*1000)
        self.config = {'trains': [{'id':'a','name':'A','hub_name':'A','tags':['01020304'],'forward_sign':-1}],
                       'readers': [{'id':rid,'name':rid,'node':rid,'distance_to_next_m':2}
                                   for rid in ('p','q','r')]}
        self.tracker = LoopTracker(self.config, clock=lambda:self.clock)

    def read(self, reader, seconds, seq=1):
        self.clock = 10+seconds
        return self.tracker.ingest(json.dumps({'reader':reader,'node':reader,'uid':'01020304',
                'boot':'boot','seq':seq,'t_ms':int(seconds*1000),
                'unix_ms':self.epoch+int(seconds*1000)}), reader)

    def test_polarity_and_signed_throttle(self):
        self.assertEqual(motor_speed(self.config['trains'][0], 40), -40)
        self.assertEqual(motor_speed(self.config['trains'][0], 0), 0)
        self.assertEqual(motor_speed(self.config['trains'][0], -50), 50)

    def test_prediction_never_changes_confirmed_block(self):
        self.assertIsNone(self.tracker.position('a'))
        self.read('p',0)
        self.read('q',2)
        self.clock = 13
        p = self.tracker.position('a')
        self.assertEqual(p['speed'],1)
        self.assertEqual(p['fraction'],.5)
        self.clock = 20
        p = self.tracker.position('a')
        self.assertTrue(p['overdue'])
        self.assertEqual((p['from'],p['to'],p['fraction']),('q','r',1))
        self.read('r',11)
        self.assertEqual(self.tracker.position('a')['to'],'p')

    def test_skipped_reader_disables_speed_but_localizes(self):
        self.read('p',0)
        self.read('r',3)
        self.assertIsNone(self.tracker.position('a')['speed'])
        self.assertEqual(self.tracker.position('a')['from'],'r')

    def test_throttle_change_clears_estimate(self):
        self.tracker.set_command('a',40)
        self.read('p',0)
        self.read('q',2)
        self.tracker.set_command('a',40)
        self.assertEqual(self.tracker.position('a')['speed'],1)
        self.tracker.set_command('a',0)
        self.assertIsNone(self.tracker.position('a')['speed'])

    def test_atomic_config_validation_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'fleet.json'
            save_config(path,self.config)
            before = path.read_text()
            self.config['trains'][0]['forward_sign'] = 0
            with self.assertRaises(ValueError):
                save_config(path,self.config)
            self.assertEqual(path.read_text(),before)

    def test_two_point_loop_and_wrap(self):
        self.config['readers'].pop()
        self.tracker = LoopTracker(self.config,clock=lambda:self.clock)
        self.read('p',0)
        self.read('q',2)
        self.read('p',4,seq=2)
        self.assertEqual(self.tracker.position('a')['speed'],1)
        self.assertEqual(self.tracker.position('a')['to'],'q')

    def test_reverse_checkpoint_speed_and_prediction(self):
        self.tracker.set_command('a', -40)
        self.read('p', 0)
        self.assertEqual(self.tracker.block_for('a'), 'r')
        self.read('r', 2)
        self.clock = 13
        p = self.tracker.position('a')
        self.assertEqual((p['from'], p['to'], p['direction']), ('q','r',-1))
        self.assertEqual(p['speed'], 1)
        self.assertEqual(p['fraction'], .5)
        self.clock = 20
        p = self.tracker.position('a')
        self.assertEqual(p['fraction'], 0)
        self.assertTrue(p['overdue'])
        self.assertEqual(self.tracker.block_for('a'), 'q')

    def test_direction_change_retains_block_and_discards_old_sample(self):
        self.tracker.set_command('a',40)
        self.read('p',0)
        self.read('q',2)
        self.clock = 13
        self.tracker.set_command('a',-40)
        self.assertEqual(self.tracker.block_for('a'),'q')
        self.assertEqual(self.tracker.position('a')['fraction'],.5)
        self.assertNotIn('a',self.tracker.speeds)
        self.assertNotIn('a',self.tracker.passages)
        self.read('q',4,seq=2)
        self.assertEqual(self.tracker.block_for('a'),'p')
        self.assertIsNone(self.tracker.position('a')['speed'])

    def test_prediction_integrates_braking_and_excludes_wait_time(self):
        self.tracker.set_command('a',40)
        self.read('p',0)
        self.read('q',2)
        self.assertAlmostEqual(self.tracker.speed_per_throttle['a'],.025)
        self.clock=12.5
        self.tracker.set_command('a',20)
        self.assertAlmostEqual(self.tracker.position('a')['fraction'],.25)
        self.clock=13.5
        self.assertAlmostEqual(self.tracker.position('a')['fraction'],.5)
        self.tracker.set_command('a',0)
        self.clock=30
        self.assertAlmostEqual(self.tracker.position('a')['fraction'],.5)
        self.tracker.set_command('a',20)
        self.clock=31
        self.assertAlmostEqual(self.tracker.position('a')['fraction'],.75)

    def test_calibration_uses_throttle_exposure_not_stopped_time(self):
        self.tracker.set_command('a',40)
        self.read('p',0)
        self.clock=11
        self.tracker.set_command('a',0)
        self.clock=20
        self.tracker.set_command('a',20)
        self.read('q',12)
        self.assertAlmostEqual(self.tracker.speeds['a'],2/12)
        self.assertAlmostEqual(self.tracker.speed_per_throttle['a'],2/(40*1+20*2))
