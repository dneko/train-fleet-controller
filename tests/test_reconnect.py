import asyncio
import copy
import queue
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fleet_controller import Backend, TrainConnection


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_scan_and_disconnect_retry_and_always_start_zero(self):
        writes, events = [], []
        connection = TrainConnection(dict(id='a', hub_name='A'), events.append)
        connection.last_speed = 75
        connection.pending = 80
        connection.accepting = True

        class Client:
            def __init__(self, device):
                self.is_connected = True
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def start_notify(self, uuid, callback):
                callback(None, b'\x01rdy\n')
            async def write_gatt_char(self, uuid, data, response):
                writes.append(data)
                self.is_connected = False

        scanner = AsyncMock(side_effect=[None, object(), object()])
        bleak = SimpleNamespace(BleakClient=Client, BleakScanner=SimpleNamespace(find_device_by_name=scanner))
        sleep = AsyncMock(side_effect=[None, None, asyncio.CancelledError()])
        with patch.dict(sys.modules, {'bleak': bleak}), patch('train_controllers.base.asyncio.sleep', sleep):
            with self.assertRaises(asyncio.CancelledError):
                await connection.run_reconnecting()
        self.assertEqual(writes, [b'\x06s0\n', b'\x06s0\n'])
        self.assertEqual(scanner.await_count, 3)
        self.assertFalse(connection.accepting)
        self.assertEqual(connection.last_speed, 0)
        self.assertTrue(any('retrying' in e[2] for e in events if e[0] == 'status'))

    async def test_lost_ready_recovers_with_zero_probe(self):
        events, writes = [], []
        connection = TrainConnection(dict(id='a', hub_name='A'), events.append)

        class Client:
            def __init__(self, device):
                self.is_connected = True
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def start_notify(self, uuid, callback):
                self.callback = callback  # Startup rdy was lost.
            async def write_gatt_char(self, uuid, data, response):
                writes.append(data)
                self.callback(None, b'\x01rdy\n')
                if len(writes) == 2:
                    self.is_connected = False

        async def wait_for(awaitable, timeout):
            if timeout == 5 and not writes:
                awaitable.close()
                raise asyncio.TimeoutError()
            return await awaitable

        bleak = SimpleNamespace(BleakClient=Client, BleakScanner=SimpleNamespace(find_device_by_name=AsyncMock(return_value=object())))
        with patch.dict(sys.modules, {'bleak': bleak}), patch('train_controllers.pybricks.asyncio.wait_for', wait_for):
            await connection.run()
        self.assertEqual(writes, [b'\x06s0\n', b'\x06s0\n'])
        self.assertIn(('ready', 'a', True), events)

    async def test_repeated_missing_reply_exits_session_for_retry(self):
        events, writes = [], []
        connection = TrainConnection(dict(id='a', hub_name='A'), events.append)

        class Client:
            def __init__(self, device):
                self.is_connected = True
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def start_notify(self, *args):
                pass
            async def write_gatt_char(self, uuid, data, response):
                writes.append(data)

        async def wait_for(awaitable, timeout):
            if timeout == 5:
                awaitable.close()
                raise asyncio.TimeoutError()
            return await awaitable

        bleak = SimpleNamespace(BleakClient=Client, BleakScanner=SimpleNamespace(find_device_by_name=AsyncMock(return_value=object())))
        with patch.dict(sys.modules, {'bleak': bleak}), patch('train_controllers.pybricks.asyncio.wait_for', wait_for):
            await connection.run()
        self.assertEqual(writes, [b'\x06s0\n'])
        self.assertIn('did not reply', connection.last_connection_status)
        self.assertNotIn(('ready', 'a', True), events)

    async def test_backend_live_edit_preserves_other_writer_and_shutdown_cancels_retry(self):
        backend = Backend.__new__(Backend)
        backend.loop = asyncio.get_running_loop()
        backend.events = queue.Queue()
        backend.demo = False
        backend.updates = set()
        backend.settings_lock = asyncio.Lock()
        backend.config = dict(trains=[dict(id=t, hub_name=t, tags=[]) for t in ('a', 'b')])
        backend.trains = backend.connections(backend.config)
        gate = asyncio.Event()
        for connection in backend.trains.values():
            connection.run_reconnecting = AsyncMock(side_effect=gate.wait)
        backend.tasks = {}
        backend.connect('a')
        backend.connect('a')
        backend.connect('b')
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        original = dict(backend.tasks)
        self.assertEqual(backend.trains['a'].run_reconnecting.await_count, 1)
        updated = copy.deepcopy(backend.config)
        updated['trains'][0]['tags'] = ['01020304']
        backend.update_trains(updated)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(backend.tasks, original)
        self.assertEqual(backend.trains['a'].train['tags'], ['01020304'])
        updated['trains'][0]['hub_name'] = 'New A'
        backend.update_trains(updated)
        await asyncio.sleep(.7)
        self.assertTrue(original['a'].cancelled())
        self.assertIs(backend.tasks['b'], original['b'])
        self.assertEqual(backend.trains['a'].run_reconnecting.await_count, 2)
        await backend.shutdown()
        self.assertTrue(all(t.done() for t in backend.tasks.values()))
        self.assertFalse(backend.updates)

    async def test_external_train_never_scans(self):
        connection = TrainConnection(dict(id='p', control_mode='external'), lambda _:None)
        await connection.run_reconnecting()
