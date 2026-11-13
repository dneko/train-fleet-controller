import copy
import http.client
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from fleet_setup import save_config
from fleet_model import Tracker
from loop_tracking import LoopTracker
from fleet_automation import Supervisor
from reader_server import create_server
from sensor_registry import SensorRegistry, bind_sensor


CONFIG = {'trains': [{'id': 'a', 'name': 'A', 'hub_name': 'A', 'tags': ['01020304']}],
          'readers': [{'id': 'gate_a', 'name': 'Gate A', 'node': ''},
                      {'id': 'gate_b', 'name': 'Gate B', 'node': ''}]}
STATUS = {'node': 'esp32-abc', 'pn532': True, 'rssi': -55, 'uptime_ms': 1000,
          'poll_age_ms': 20, 'identify_ack': 0, 'led': 'blink'}


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.now = 10.
        self.registry = SensorRegistry(CONFIG, clock=lambda: self.now)

    def test_new_sensor_rate_offline_and_command_ack_expiry(self):
        result = self.registry.heartbeat(STATUS, '192.168.1.20')
        self.assertEqual(result['gate'], '')
        command = self.registry.identify('esp32-abc')
        self.now += .1
        result = self.registry.heartbeat(STATUS, '192.168.1.20')
        self.assertEqual(result['identify_id'], command)
        self.assertLessEqual(result['identify_ms'], 9900)
        self.assertTrue(self.registry.snapshot()[0][0]['identify_pending'])
        self.now += .1
        self.registry.heartbeat(dict(STATUS, identify_ack=command), '192.168.1.20')
        row = self.registry.snapshot()[0][0]
        self.assertTrue(row['identifying'])
        self.assertAlmostEqual(row['hz'], 10.)
        self.now += 2
        self.assertFalse(self.registry.snapshot()[0][0]['online'])
        with self.assertRaises(ValueError):
            self.registry.identify('esp32-abc')
        self.now += 10
        self.assertEqual(self.registry.heartbeat(STATUS, '192.168.1.20')['identify_ms'], 0)

    def test_gate_binding_moves_replaces_unassigns_and_persists(self):
        first = bind_sensor(CONFIG, 'esp32-abc', 'gate_a')
        moved = bind_sensor(first, 'esp32-abc', 'gate_b')
        self.assertEqual([r['node'] for r in moved['readers']], ['', 'esp32-abc'])
        with self.assertRaises(ValueError):
            bind_sensor(moved, 'esp32-def', 'gate_b')
        replaced = bind_sensor(moved, 'esp32-def', 'gate_b', replace=True)
        with tempfile.TemporaryDirectory() as directory:
            saved = save_config(Path(directory) / 'fleet.json', replaced)
        restored = SensorRegistry(saved)
        self.assertEqual(restored.gate_for('esp32-def'), 'gate_b')
        self.assertIsNone(restored.gate_for('esp32-abc'))
        restored.configure(bind_sensor(saved, 'esp32-def', ''))
        self.assertIsNone(restored.gate_for('esp32-def'))
        self.assertEqual(CONFIG['readers'][0]['node'], '')

    def test_invalid_status_does_not_create_sensor(self):
        for fields in ({'node': 'bad\r\nid'}, {'pn532': 'yes'}, {'rssi': 500}, {'poll_age_ms': -1}):
            with self.assertRaises(ValueError):
                self.registry.heartbeat(dict(STATUS, **fields), '127.0.0.1')
        self.assertEqual(self.registry.snapshot()[0], [])

    def test_no_led_and_bounded_recent_tags(self):
        self.registry.heartbeat(dict(STATUS, led='none'), '127.0.0.1')
        with self.assertRaises(ValueError):
            self.registry.identify('esp32-abc')
        for i in range(105):
            self.registry.record_tag({'node': 'esp32-abc', 'uid': f'{i:08X}'})
        self.assertEqual(len(self.registry.snapshot()[1]), 100)

    def test_segment_led_tracks_confirmed_occupancy_and_multiple_trains(self):
        config = bind_sensor(CONFIG, 'esp32-abc', 'gate_a')
        config = bind_sensor(config, 'esp32-def', 'gate_b')
        config['trains'].append({'id': 'b', 'name': 'B', 'hub_name': 'B', 'tags': ['05060708']})
        tracker = LoopTracker(config, clock=lambda: self.now)
        supervisor = Supervisor(config, tracker)
        registry = SensorRegistry(config, clock=lambda: self.now)

        def detect(gate, uid):
            self.now += 2
            result = tracker.ingest(json.dumps({'reader': gate, 'uid': uid}), gate)
            supervisor.observe(result[0])
            registry.set_occupancy(supervisor.occupied())

        def light(node):
            return registry.heartbeat(dict(STATUS, node=node), '127.0.0.1')['occupied']

        self.assertFalse(light('esp32-abc'))
        detect('gate_a', '01020304')
        self.assertTrue(light('esp32-abc'))
        self.assertFalse(light('esp32-def'))
        registry.identify('esp32-abc')
        self.now += 100  # No timeout, stop, or prediction releases occupancy.
        registry.set_occupancy(supervisor.occupied())
        self.assertTrue(light('esp32-abc'))
        self.assertFalse(registry.snapshot()[0][0]['identifying'])
        detect('gate_a', '05060708')
        detect('gate_b', '01020304')
        self.assertTrue(light('esp32-abc'))  # Second train remains.
        self.assertTrue(light('esp32-def'))
        detect('gate_b', '05060708')
        self.assertFalse(light('esp32-abc'))
        self.assertTrue(light('esp32-def'))
        supervisor.remove_from_track('a')
        registry.set_occupancy(supervisor.occupied())
        self.assertTrue(light('esp32-def'))
        supervisor.remove_from_track('b')
        registry.set_occupancy(supervisor.occupied())
        self.assertFalse(light('esp32-def'))
        self.assertFalse(light('esp32-unassigned'))


class SensorHTTPTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(CONFIG)
        self.registry = SensorRegistry(self.config)
        self.events = queue.Queue()
        self.server = create_server('127.0.0.1', 0, 'test-token-12345678', {'gate_a', 'gate_b'}, self.events, self.registry)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()

    def post(self, path, data, token='test-token-12345678'):
        self.client.request('POST', path, json.dumps(data), {'Authorization': 'Bearer ' + token})
        response = self.client.getresponse()
        body = response.read()
        return response.status, dict(response.getheaders()), body

    def test_live_status_identify_and_node_routing(self):
        status, _, body = self.post('/status', STATUS)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['gate'], '')
        self.assertTrue(self.events.empty())  # Heartbeats don't flood GUI event queue.
        command = self.registry.identify('esp32-abc')
        status, headers, _ = self.post('/status', STATUS)
        self.assertEqual(headers['X-Identify-Id'], str(command))
        self.post('/status', dict(STATUS, identify_ack=command))
        self.assertTrue(self.registry.snapshot()[0][0]['identifying'])
        event = {'node': 'esp32-abc', 'uid': '01:02:03:04', 'boot': 'boot1', 'seq': 1, 't_ms': 100}
        self.assertEqual(self.post('/events', event)[0], 202)
        self.assertTrue(self.events.empty())
        self.assertEqual(self.registry.snapshot()[1][0]['uid'], '01020304')
        self.config = bind_sensor(self.config, 'esp32-abc', 'gate_b')
        self.registry.configure(self.config)
        # Even if firmware claims gate_a, the Mac's assignment chooses gate_b.
        self.assertEqual(self.post('/events', dict(event, reader='gate_a'))[0], 202)
        _, gate, payload = self.events.get_nowait()
        self.assertEqual(gate, 'gate_b')
        self.assertEqual(Tracker(self.config).ingest(payload, gate), ('a', '01020304', 'Gate B'))
        self.assertEqual(json.loads(payload)['time_source'], 'controller_received')
        self.registry.set_occupancy({'gate_b': {'a'}})
        _, headers, body = self.post('/status', STATUS)
        self.assertEqual(headers['X-Segment-Occupied'], '1')
        self.assertTrue(json.loads(body)['occupied'])
        self.registry.set_occupancy({})
        self.assertEqual(self.post('/status', STATUS)[1]['X-Segment-Occupied'], '0')

    def test_bad_auth_and_malformed_status_are_rejected(self):
        self.assertEqual(self.post('/status', STATUS, token='wrong')[0], 401)
        self.assertEqual(self.registry.snapshot()[0], [])
        self.assertEqual(self.post('/status', dict(STATUS, pn532=None))[0], 400)
        self.assertEqual(self.post('/events', {'node': 'esp32-abc', 'uid': 'garbage'})[0], 400)
        self.assertEqual(self.registry.snapshot()[1], [])
