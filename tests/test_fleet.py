import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from fleet_model import load_config, normalize_uid, Tracker
from fleet_controller import TrainConnection


class FleetTests(unittest.TestCase):
    def config(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fleet.json'
            path.write_text(json.dumps(data))
            return load_config(path)

    def test_duplicate_tag_rejected_after_normalization(self):
        with self.assertRaises(ValueError):
            self.config({'trains': [
                {'id': 'a', 'name': 'A', 'hub_name': 'A', 'tags': ['01:02:03:04']},
                {'id': 'b', 'name': 'B', 'hub_name': 'B', 'tags': ['01020304']} ]})

    def test_uid_validation(self):
        self.assertEqual(normalize_uid('aa:00:bc:12'), 'AA00BC12')
        for value in (None, 'ZZ000000', '123', 1234):
            with self.assertRaises(ValueError):
                normalize_uid(value)

    def test_tracking_and_duplicate_reads(self):
        config = self.config({'trains': [{'id': 'a', 'name': 'A', 'hub_name': 'A',
                                          'tags': ['01020304']}],
                              'readers': [{'id': 's', 'name': 'Station'},
                                          {'id': 'y', 'name': 'Yard'}]})
        now = [10.0]
        tracker = Tracker(config, clock=lambda: now[0])
        def event(reader='s', uid='01020304'):
            return json.dumps({'reader': reader, 'uid': uid})
        self.assertEqual(tracker.ingest(event(), 's'), ('a', '01020304', 'Station'))
        now[0] += .2
        self.assertIsNone(tracker.ingest(event(), 's'))
        self.assertEqual(tracker.ingest(event('y'), 'y')[2], 'Yard')
        self.assertEqual(tracker.last_seen['a'][0], 'y')
        self.assertIsNone(tracker.ingest(event(uid='FFFFFFFF'), 's')[0])
        with self.assertRaises(ValueError):
            tracker.ingest(event(), 'y')
        now[0] += 2
        self.assertIsNotNone(tracker.ingest(event(), 's'))

    def test_bad_config(self):
        for config in ({'trains': 'a'}, {'readers': [42]}, {'trains': [
            {'id': 'a', 'name': 'A', 'hub_name': 'A', 'tags': '01020304'}]}):
            with self.assertRaises(ValueError):
                self.config(config)

    def test_speed_coalescing_and_stop_priority(self):
        train = TrainConnection({'id': 'a'}, lambda _: None)
        train.set_speed(50)
        self.assertIsNone(train.pending)  # No commands before initial zero.
        train.accepting = True
        for value in range(1, 100):
            train.set_speed(value)
        self.assertEqual(train.pending, 99)
        train.set_speed(0)
        train.set_speed(20)
        self.assertTrue(train.stop_pending)
        self.assertEqual(train.pending, 20)

    def test_fragmented_ready_notification(self):
        train = TrainConnection({'id': 'a'}, lambda _: None)
        for payload in (b'', b'\x00rdy', b'\x01r', b'\x01d'):
            train.receive(None, payload)
            self.assertFalse(train.ready.is_set())
        train.receive(None, b'\x01y\r\n')
        self.assertTrue(train.ready.is_set())

    def test_waiting_command_requires_capability_and_zero_speed(self):
        train = TrainConnection({'id':'a'}, lambda _:None)
        train.set_waiting(True)
        self.assertEqual(train.speed_command(0),b'\x06s0\n')
        train.receive(None,b'\x01features waiting-led\nrdy\n')
        self.assertEqual(train.speed_command(0),b'\x06s0w1\n')
        self.assertEqual(train.speed_command(20),b'\x06s20\n')
        train.set_waiting(False)
        self.assertEqual(train.speed_command(0),b'\x06s0\n')

    def test_waiting_state_cannot_displace_a_pending_motor_stop(self):
        train = TrainConnection({'id':'a'}, lambda _:None)
        train.accepting=True
        train.last_speed=40
        train.set_speed(0)
        train.set_waiting(True)
        self.assertTrue(train.stop_pending)
        self.assertEqual(train.pending,0)

    def test_invalid_auto_speed_and_view_settings_are_rejected(self):
        for maximum, target in ((0,0),(-1,0),(10,11),(float('nan'),0),(10,float('inf'))):
            with self.assertRaises(ValueError):
                self.config({'automation':{'max_speed_studs_s':maximum,'target_speed_studs_s':target}})
        with self.assertRaises(ValueError):
            self.config({'view':{'distances':'yes'}})
        config = self.config({'automation':{'max_speed_studs_s':12,'target_speed_studs_s':8},
                              'view':{'radii':False}})
        self.assertEqual(config['automation']['max_speed_studs_s'],12)

class SpeedTests(unittest.TestCase):
    def setup_tracker(self, confirmed=True):
        self.now = 100
        self.epoch = int(__import__('time').time() * 1000)
        config = {'trains': [{'id': 'a', 'tags': ['01020304']}],
                  'readers': [{'id': 'a', 'name': 'A', 'node': 'na'},
                              {'id': 'b', 'name': 'B', 'node': 'nb'}],
                  'segments': [{'from': 'a', 'to': 'b', 'distance_m': 2,
                                'unambiguous': confirmed}]}
        return Tracker(config, clock=lambda: self.now)

    def event(self, reader, offset, synced=True, seq=1):
        return json.dumps({'reader': reader, 'uid': '01020304', 'node': 'n'+reader,
                           'boot': 'test', 'seq': seq, 't_ms': offset + 1000,
                           'unix_ms': self.epoch + offset if synced else None})

    def test_speed_uses_detection_time_not_arrival_delay(self):
        t = self.setup_tracker()
        t.ingest(self.event('a', 0), 'a')
        self.now += 10  # Simulated network delay does not change 2m / 2s.
        t.ingest(self.event('b', 2000), 'b')
        self.assertIn('~125.00 studs/s', t.measurements['a'])
        self.assertIn('A → B', t.measurements['a'])
        self.assertIsNone(t.ingest(self.event('a', 0), 'a'))
        self.assertEqual(t.last_seen['a'][0], 'b')

    def test_reverse_direction(self):
        t = self.setup_tracker()
        t.ingest(self.event('b', 0), 'b')
        t.ingest(self.event('a', 2000), 'a')
        self.assertIn('B → A', t.measurements['a'])

    def test_ambiguous_route_and_unsynchronized_clocks(self):
        t = self.setup_tracker(False)
        t.ingest(self.event('a', 0), 'a')
        t.ingest(self.event('b', 2000), 'b')
        self.assertNotIn('m/s', t.measurements['a'])
        t = self.setup_tracker()
        t.ingest(self.event('a', 0, False), 'a')
        t.ingest(self.event('b', 2000, False), 'b')
        self.assertIn('speed unknown', t.measurements['a'])

    def test_reject_wrong_node_and_stale_clock(self):
        t = self.setup_tracker()
        event = json.loads(self.event('a', 0))
        event['node'] = 'wrong'
        with self.assertRaises(ValueError):
            t.ingest(json.dumps(event), 'a')
        with self.assertRaises(ValueError):
            t.ingest(self.event('a', 60000), 'a')


if __name__ == '__main__':
    unittest.main()
