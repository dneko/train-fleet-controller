import json
import queue
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from fleet_setup import register_tag, save_config
from fleet_model import Tracker
from reader_server import create_server
import tempfile
import time
from pathlib import Path


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.config = {'trains': [
            {'id': 'a', 'name': 'Train A', 'hub_name': 'A', 'tags': []},
            {'id': 'b', 'name': 'Train B', 'hub_name': 'B', 'tags': []}],
            'readers': [{'id': 'gate_a', 'name': 'Gate A', 'node': 'node_a'}]}

    def test_registration_normalizes_persists_and_identifies(self):
        register_tag(self.config, 'a', '04:ab:00:12')
        register_tag(self.config, 'a', '04AB0012')
        self.assertEqual(self.config['trains'][0]['tags'], ['04AB0012'])
        with tempfile.TemporaryDirectory() as directory:
            saved = save_config(Path(directory) / 'fleet.json', self.config)
        tracker = Tracker(saved)
        result = tracker.ingest(json.dumps({'reader': 'gate_a', 'uid': '04AB0012'}), 'gate_a')
        self.assertEqual(result, ('a', '04AB0012', 'Gate A'))

    def test_cannot_steal_tag_or_assign_invalid_uid(self):
        register_tag(self.config, 'a', '04AB0012')
        for target, uid in [('b', '04:ab:00:12'), ('a', 'bad'), ('missing', '01020304')]:
            with self.assertRaises(ValueError):
                register_tag(self.config, target, uid)
        self.assertEqual(self.config['trains'][1]['tags'], [])

    def test_network_event_reaches_registered_train(self):
        register_tag(self.config, 'b', '04AB0012')
        events = queue.Queue()
        server = create_server('127.0.0.1', 0, 'test-token-12345678', {'gate_a'}, events)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            payload = json.dumps({'reader': 'gate_a', 'node': 'node_a', 'uid': '04AB0012',
                                  'boot': 'test', 'seq': 1, 't_ms': 123, 'unix_ms': None}).encode()
            url = f'http://127.0.0.1:{server.server_port}/events'
            request = Request(url, data=payload, headers={'Authorization': 'Bearer test-token-12345678'})
            with urlopen(request, timeout=2) as response:
                self.assertEqual(response.status, 202)
            kind, reader, body = events.get_nowait()
            self.assertEqual(kind, 'rfid')
            received = json.loads(body)
            self.assertEqual(received['time_source'], 'controller_received')
            self.assertLess(abs(received['unix_ms'] - time.time_ns() // 1_000_000), 2000)
            self.assertEqual(Tracker(self.config).ingest(body, reader), ('b', '04AB0012', 'Gate A'))
            with self.assertRaises(HTTPError) as rejected:
                urlopen(Request(url, data=payload), timeout=2)
            self.assertEqual(rejected.exception.code, 401)
            self.assertTrue(events.empty())
        finally:
            server.shutdown()
            server.server_close()
            worker.join()
