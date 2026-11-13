from contextlib import ExitStack
import asyncio
import copy
import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from battery_monitor import BatteryReading
from fleet_controller import Backend, TrainConnection
from fleet_model import load_config
from fleet_setup import save_config
from train_controllers import (controller_spec, create_controller, supports, history_identity,
                               register_controller)
from train_controllers.pybricks import PybricksController
from controller_fakes import OutputController, OtherOutputController, output_train, registered_outputs


def mixed_config():
    return dict(trains=[dict(id='hub', name='Hub', hub_name='Train A', tags=[]), output_train(),
                        dict(id='external', name='External', control_mode='external', tags=[])], readers=[])


class ControllerConfigurationTests(unittest.TestCase):
    def setUp(self):
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        contexts.enter_context(registered_outputs())
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)/'config.json'

    def load(self, config):
        self.path.write_text(json.dumps(config))
        return load_config(self.path)

    def test_legacy_config_and_connection_import_remain_compatible(self):
        config = dict(trains=[mixed_config()['trains'][0]], readers=[])
        before = copy.deepcopy(config)
        loaded = self.load(config)
        self.assertEqual(loaded, before)
        self.assertEqual(controller_spec(loaded['trains'][0]).type_id, 'pybricks')
        self.assertIs(TrainConnection, PybricksController)
        self.assertIsInstance(create_controller(loaded['trains'][0], lambda _: None), PybricksController)

    def test_mixed_config_round_trip_and_saved_driver_options_without_hub_name(self):
        config = mixed_config()
        config['saved_trains'] = [output_train('saved', '2')]
        saved = save_config(self.path, config)
        self.assertEqual(load_config(self.path), saved)
        self.assertNotIn('hub_name', saved['trains'][1])
        self.assertEqual(saved['saved_trains'][0]['controller_options']['channel'], '2')
        self.assertTrue(supports(saved['trains'][0], 'city_hub_program'))
        self.assertFalse(supports(saved['trains'][1], 'city_hub_program'))
        self.assertFalse(supports(saved['trains'][1], 'power_telemetry'))
        self.assertFalse(supports(saved['trains'][2], 'power_telemetry'))

    def test_unknown_driver_bad_options_and_duplicate_endpoints_rejected(self):
        for changes in ({'controller_type': 'missing'}, {'controller_type': []},
                        {'controller_options': []}, {'controller_options': {'port': '', 'channel': '1'}},
                        {'controller_options': {'port': 'p', 'channel': '7'}}):
            with self.subTest(changes=changes):
                config = mixed_config()
                config['trains'][1].update(changes)
                with self.assertRaises(ValueError):
                    self.load(config)
        config = mixed_config()
        config['saved_trains'] = [output_train('saved')]
        with self.assertRaisesRegex(ValueError, 'unique'):
            self.load(config)
        config['saved_trains'][0]['controller_type'] = 'missing'
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            self.load(config)

    def test_explicit_type_is_same_identity_and_options_change_it(self):
        train = mixed_config()['trains'][0]
        self.assertEqual(history_identity(train), history_identity(dict(train, controller_type='pybricks')))
        a = output_train()
        b = copy.deepcopy(a)
        b['controller_options']['channel'] = '2'
        self.assertNotEqual(history_identity(a), history_identity(b))
        with self.assertRaisesRegex(ValueError, 'only once'):
            register_controller(controller_spec(a))

    def test_backend_startup_failure_is_reported_without_waiting_forever(self):
        config = mixed_config()
        config['trains'][1]['controller_type'] = 'missing'
        with self.assertRaisesRegex(ValueError, 'Unknown'):
            Backend(config, queue.Queue(), False)

    def test_track_voltage_can_disable_battery_thresholds(self):
        reading = BatteryReading(3000, 500, 100, low_voltage_mv=None, critical_voltage_mv=None)
        self.assertEqual(reading.warning_level, '')
        self.assertEqual(reading.warning(), '')
        self.assertEqual(reading.watts, 1.5)


class ControllerBackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        contexts.enter_context(registered_outputs())
        self.backend = Backend.__new__(Backend)
        self.backend.loop = asyncio.get_running_loop()
        self.backend.events = queue.Queue()
        self.backend.demo = False
        self.backend.updates = set()
        self.backend.settings_lock = asyncio.Lock()
        self.backend.config = mixed_config()
        self.backend.trains = self.backend.connections(self.backend.config)
        self.backend.tasks = {}
        self.addAsyncCleanup(self.backend.shutdown)

    async def flush(self):
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def test_dispatch_no_ble_requirement_and_optional_features_are_not_called(self):
        backend = self.backend
        self.assertEqual(set(backend.trains), {'hub', 'track'})
        track = backend.trains['track']
        self.assertIsInstance(track, OutputController)
        track.set_waiting = Mock()
        with patch.dict('sys.modules', bleak=None):
            backend.connect('track')
            backend.connect('track')
            await self.flush()
            backend.speed('track', -35)
            backend.waiting('track', True)
            backend.record_history(True)
            backend.connect('external')
            backend.speed('external', 50)
            await self.flush()
        self.assertEqual(track.writes, [0, -35])
        self.assertEqual(len(track.sessions), 1)
        self.assertFalse(track.recording_history)
        self.assertTrue(backend.trains['hub'].recording_history)
        track.set_waiting.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'Pybricks'):
            backend.upload_program('track', 'source')
        self.assertFalse(backend.tasks['track'].done())
        await backend.shutdown()
        self.assertEqual(track.closed, 1)
        self.assertEqual(track.writes[-1], 0)
        self.assertFalse(track.accepting)

    async def test_options_restart_only_changed_connection_and_type_replaces_driver(self):
        backend = self.backend
        backend.config['trains'].insert(2, output_train('second', '2'))
        backend.trains = backend.connections(backend.config)
        for tid in ('track', 'second'):
            backend.connect(tid)
        await self.flush()
        first = backend.trains['track']
        other_task = backend.tasks['second']
        backend.speed('track', 30)
        await self.flush()
        edited = copy.deepcopy(backend.config)
        edited['trains'][1]['controller_options']['port'] = 'changed-port'
        backend.update_trains(edited)
        await self.flush()
        await asyncio.gather(*backend.updates)
        await self.flush()
        self.assertEqual(first.writes, [0, 30, 0, 0, 0])
        self.assertEqual(first.sessions[-1]['port'], 'changed-port')
        self.assertIs(backend.tasks['second'], other_task)
        edited = copy.deepcopy(edited)
        edited['trains'][1]['controller_type'] = 'test-other'
        backend.update_trains(edited)
        await self.flush()
        await asyncio.gather(*backend.updates)
        await self.flush()
        self.assertIsInstance(backend.trains['track'], OtherOutputController)
        self.assertIsNot(backend.trains['track'], first)
        self.assertEqual(backend.trains['track'].writes, [0])
        self.assertIs(backend.tasks['second'], other_task)
        self.assertFalse(other_task.done())

    async def test_hub_update_leaves_other_controller_running(self):
        backend = self.backend
        backend.connect('track')
        await self.flush()
        backend.speed('track', 30)
        await self.flush()
        task = backend.tasks['track']
        track = backend.trains['track']
        hub = backend.trains['hub']
        async def upload(**kwargs):
            hub.uploading = False
            hub.upload_status('Verified', 'complete')
            return False
        hub.run = AsyncMock(side_effect=upload)
        with patch('train_controllers.pybricks_updates.scan_hubs',
                   new=AsyncMock(return_value=({'hub': object()}, {}))) as scan:
            backend.update_hubs('source')
            await self.flush()
            await backend.hub_update
        self.assertEqual([t['id'] for t in scan.call_args.args[0]], ['hub'])
        self.assertIs(backend.tasks['track'], task)
        self.assertFalse(task.done())
        self.assertEqual(track.writes, [0, 30])
        self.assertTrue(track.accepting)
