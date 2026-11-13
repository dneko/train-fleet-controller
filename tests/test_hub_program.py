"""Compile the real program and exercise uploads against a simulated BLE hub."""
import asyncio
from contextlib import contextmanager
import queue
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fleet_controller import Backend, TrainConnection
from hub_program import (CAPABILITIES_UUID, COMMAND_UUID, PNP_UUID, PROGRAM_ID,
                         PROTOCOL_UUID, compile_program, read_program, upload_program)


class Services(list):
    def get_characteristic(self, uuid):
        return object() if self else None


class Hub:
    def __init__(self, device=None):
        self.is_connected = True
        self.mtu_size = 23
        self.writes = []
        self.memory = bytearray()
        self.committed = None
        self.started = False
        self.ack_zero = True
        self.version = PROGRAM_ID
        self.fail_chunk = False
        self.zero_writes = 0
        self.close_after_zero = False
        self.running = True
        self.connected = False
        self.services = Services([SimpleNamespace(uuid='c5f50001-8280-46da-89f4-6d8051e4aeef')])
        self.characteristics = {
            PROTOCOL_UUID: b'1.3.0', PNP_UUID: struct.pack('<BHHH', 1, 0x397, 0x41, 0),
            CAPABILITIES_UUID: struct.pack('<HII', 100, 2, 4000)}

    async def __aenter__(self):
        self.connected = True
        return self

    async def __aexit__(self, *args):
        self.is_connected = False

    async def start_notify(self, uuid, callback):
        self.callback = callback
        callback(None, b'\x00' + struct.pack('<I', 64 if self.running else 0))

    async def read_gatt_char(self, uuid):
        return self.characteristics[uuid]

    async def write_gatt_char(self, uuid, data, response):
        assert uuid == COMMAND_UUID and response
        self.writes.append(data)
        command = data[0]
        if command == 0:
            if self.running:
                self.running = False
                self.callback(None, b'\x00\0\0\0\0')
        elif command == 3:
            self.committed = int.from_bytes(data[1:], 'little')
        elif command == 4:
            if self.fail_chunk:
                raise OSError('transfer interrupted')
            offset = int.from_bytes(data[1:5], 'little')
            assert offset == len(self.memory)
            self.memory.extend(data[5:])
        elif command == 1:
            self.running = True
            self.started = True
            self.callback(None, b'\x01program ' + self.version.encode() + b'\nfeatures waiting-led\nr')
            self.callback(None, b'\x01dy\n')
        elif command == 6:
            assert data == b'\x06s0\n'
            self.zero_writes += 1
            if self.ack_zero:
                self.callback(None, b'\x01rdy\n')
            if self.close_after_zero and self.zero_writes == 2:
                self.is_connected = False


class ProgramCompileTests(unittest.TestCase):
    def test_bundled_program_compiles_to_city_hub_bytecode(self):
        source = read_program()
        self.assertIn('program ' + PROGRAM_ID, source)
        program = compile_program(source)
        size = struct.unpack('<I', program[:4])[0]
        self.assertEqual(program[4:13], b'__main__\0')
        self.assertEqual(len(program[13:]), size)
        self.assertEqual(program[13:15], b'M\x06')
        self.assertLess(len(program), 4000)

    def test_syntax_error_reports_compiler_details(self):
        with self.assertRaisesRegex(ValueError, 'compile City Hub'):
            compile_program('def broken(:\n')


class ProgramUploadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events = []
        self.connection = TrainConnection(dict(id='a', hub_name='Train A'), self.events.append)
        self.connection.uploading = True
        self.hub = Hub()

    async def standalone(self, program=b'x' * 71):
        await self.hub.start_notify(COMMAND_UUID, self.connection.receive)
        await upload_program(self.hub, self.connection, program)

    @contextmanager
    def bleak(self):
        with patch.dict(sys.modules, {'bleak': SimpleNamespace(
            BleakClient=lambda _: self.hub,
            BleakScanner=SimpleNamespace(find_device_by_name=AsyncMock(return_value=object())))}), \
                patch('train_controllers.pybricks.scan_hubs', new=AsyncMock(return_value=({'a': object()}, {}))):
            yield

    async def test_transfer_obeys_mtu_and_commits_only_complete_program(self):
        program = bytes(range(71))
        await self.standalone(program)
        self.assertEqual(self.hub.memory, program)
        self.assertEqual(self.hub.committed, len(program))
        self.assertTrue(self.hub.started)
        self.assertTrue(all(len(data) <= 20 for data in self.hub.writes))
        self.assertEqual(self.hub.writes[0], b'\x00')
        self.assertEqual(self.hub.writes[1], b'\x03\0\0\0\0')
        self.assertEqual(self.hub.writes[-1], b'\x06s0\n')

    async def test_modern_capabilities_with_slot_use_slot_zero(self):
        self.hub.characteristics[PROTOCOL_UUID] = b'1.5.0'
        self.hub.characteristics[CAPABILITIES_UUID] = struct.pack('<HIIB', 100, 32, 4000, 1)
        await self.standalone()
        self.assertIn(b'\x01\0', self.hub.writes)

    async def test_rejects_incompatible_or_oversized_upload_before_any_write(self):
        cases = [(PROTOCOL_UUID, b'1.2.0'), (PROTOCOL_UUID, b'2.0.0'),
                 (PNP_UUID, struct.pack('<BHHH', 1, 0x397, 0x80, 0)),
                 (CAPABILITIES_UUID, struct.pack('<HII', 100, 0, 4000)),
                 (CAPABILITIES_UUID, struct.pack('<HII', 100, 2, 30))]
        for uuid, value in cases:
            with self.subTest(uuid=uuid, value=value):
                self.hub = Hub()
                self.hub.characteristics[uuid] = value
                with self.assertRaises(ValueError):
                    await self.standalone()
                self.assertEqual(self.hub.writes, [])

    async def test_interrupted_transfer_never_starts_or_commits_partial_program(self):
        self.hub.fail_chunk = True
        with self.bleak(), self.assertRaisesRegex(OSError, 'interrupted'):
            await self.connection.run(b'x' * 71)
        self.assertEqual(self.hub.committed, 0)
        self.assertFalse(self.hub.started)
        self.assertEqual(self.hub.writes[-1], b'\x00')
        self.assertNotIn(('ready', 'a', True), self.events)
        self.assertFalse(self.connection.accepting)

    async def test_wrong_program_response_is_failure_and_stops_program(self):
        self.hub.version = 'another-program'
        with self.bleak(), self.assertRaisesRegex(RuntimeError, 'did not start'):
            await self.connection.run(b'x' * 71)
        self.assertEqual(self.hub.writes[-1], b'\x00')
        self.assertNotIn(('ready', 'a', True), self.events)

    async def test_zero_ack_required_and_cancellation_stops_hub(self):
        self.hub.ack_zero = False
        with self.bleak():
            task = asyncio.create_task(self.connection.run(b'x' * 71))
            for _ in range(100):
                if self.hub.zero_writes:
                    break
                await asyncio.sleep(.001)
            self.assertEqual(self.hub.zero_writes, 1)
            self.assertFalse(self.connection.accepting)
            self.assertFalse(any(e[0] == 'program_upload' and e[2] == 'complete' for e in self.events))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.hub.writes[-1], b'\x00')

    async def test_verified_upload_transitions_to_zeroed_motor_session(self):
        self.hub.close_after_zero = True
        with self.bleak():
            self.assertTrue(await self.connection.run(b'x' * 71))
        complete = next(i for i,e in enumerate(self.events) if e[0] == 'program_upload' and e[2] == 'complete')
        ready = self.events.index(('ready', 'a', True))
        self.assertLess(complete, ready)
        self.assertEqual([d for d in self.hub.writes if d[0] == 6], [b'\x06s0\n', b'\x06s0\n'])

    async def test_uploading_rejects_nonzero_commands(self):
        self.connection.accepting = True
        self.connection.set_speed(50)
        self.assertIsNone(self.connection.pending)
        self.connection.set_speed(0)
        self.assertTrue(self.connection.stop_pending)

    async def test_empty_hub_connects_before_compiling_and_needs_no_existing_program_reply(self):
        self.hub.running = False
        self.hub.close_after_zero = True

        def compile_connected(source):
            self.assertTrue(self.hub.connected)
            self.assertFalse(self.connection.ready.is_set())
            self.assertEqual(source, 'source')
            return b'x' * 71

        with self.bleak(), patch('train_controllers.pybricks.compile_program', side_effect=compile_connected):
            self.assertTrue(await self.connection.run(source='source'))
        self.assertTrue(self.hub.started)
        self.assertEqual(self.hub.committed, 71)
        debug = '\n'.join(e[2] for e in self.events if e[0] == 'program_debug')
        for detail in ('Scanning', 'Bluetooth connected', 'Compiling', 'MTU=', 'offset=',
                       'Hub stdout: program', 'zero-throttle acknowledgment'):
            self.assertIn(detail, debug)

    async def test_factory_firmware_is_explained_before_compilation_or_writes(self):
        self.hub.services.clear()
        with self.bleak(), patch('train_controllers.pybricks.compile_program') as compiler:
            with self.assertRaisesRegex(RuntimeError, 'factory-default LEGO hub'):
                await self.connection.run(source='source')
        compiler.assert_not_called()
        self.assertEqual(self.hub.writes, [])

    async def test_connection_failure_does_not_compile_or_write(self):
        class DisconnectedHub(Hub):
            async def __aenter__(self):
                raise OSError('Bluetooth unavailable')
        self.hub = DisconnectedHub()
        with self.bleak(), patch('train_controllers.pybricks.compile_program') as compiler:
            with self.assertRaisesRegex(OSError, 'Bluetooth unavailable'):
                await self.connection.run(source='source')
        compiler.assert_not_called()
        self.assertEqual(self.hub.writes, [])


class BackendProgramTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.backend = Backend.__new__(Backend)
        self.backend.loop = asyncio.get_running_loop()
        self.backend.events = queue.Queue()
        self.backend.demo = False
        self.backend.updates = set()
        self.backend.settings_lock = asyncio.Lock()
        self.backend.config = dict(trains=[dict(id=t, hub_name=t, tags=[]) for t in ('a', 'b')])
        self.backend.trains = self.backend.connections(self.backend.config)
        self.backend.tasks = {}

    async def test_replaces_only_target_writer_and_does_not_retry_failed_upload(self):
        stop = asyncio.Event()
        old = {tid: asyncio.create_task(stop.wait()) for tid in ('a', 'b')}
        self.backend.tasks.update(old)
        connection = self.backend.trains['a']
        async def upload(*, source, device):
            self.assertTrue(old['a'].done())
            self.assertFalse(old['b'].done())
            raise OSError('test transfer failure')
        connection.run = AsyncMock(side_effect=upload)
        connection.run_reconnecting = AsyncMock()
        with patch('train_controllers.pybricks.compile_program', return_value=b'compiled'):
            self.backend.upload_program('a', 'source')
            await asyncio.sleep(0)
            replacement = self.backend.tasks['a']
            self.backend.connect('a')
            await replacement
        self.assertIs(self.backend.tasks['a'], replacement)
        self.assertTrue(old['a'].cancelled())
        connection.run.assert_awaited_once_with(source='source', device=None)
        connection.run_reconnecting.assert_not_called()
        self.assertFalse(connection.uploading)
        events = list(self.backend.events.queue)
        self.assertTrue(any(e[0] == 'program_upload' and e[2] == 'failed' for e in events))
        await self.backend.shutdown()

    async def test_shutdown_cancels_upload_and_previous_writer(self):
        gate = asyncio.Event()
        previous = asyncio.create_task(gate.wait())
        self.backend.tasks['a'] = previous
        async def compiling(*args):
            await gate.wait()
        with patch('train_controllers.pybricks.asyncio.to_thread', new=AsyncMock(side_effect=compiling)):
            self.backend.upload_program('a', 'source')
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            await self.backend.shutdown()
        self.assertTrue(previous.cancelled())
        self.assertFalse(self.backend.trains['a'].uploading)

    async def test_demo_and_external_uploads_never_start(self):
        self.backend.demo = True
        with self.assertRaises(ValueError):
            self.backend.upload_program('a', 'source')
        self.backend.demo = False
        with self.assertRaises(ValueError):
            self.backend.upload_program('external', 'source')
        self.assertEqual(self.backend.tasks, {})

    async def test_pending_settings_reject_upload_without_losing_ready_session(self):
        gate = asyncio.Event()
        update = asyncio.create_task(gate.wait())
        self.backend.updates.add(update)
        self.backend.trains['a'].accepting = True
        self.backend.upload_program('a', 'source')
        await asyncio.sleep(0)
        self.assertEqual(self.backend.tasks, {})
        self.assertIn(('ready', 'a', True), list(self.backend.events.queue))
        self.assertFalse(self.backend.trains['a'].uploading)
        await self.backend.shutdown()

    async def test_batch_scans_active_names_once_then_updates_sequentially_and_keeps_sessions(self):
        self.backend.config['saved_trains'] = [dict(id='saved', hub_name='Saved')]
        self.backend.config['trains'].append(dict(id='external', hub_name='External', control_mode='external'))
        self.backend.trains = self.backend.connections(self.backend.config)
        gate = asyncio.Event()
        order = []
        devices = {'a': object(), 'b': object()}

        async def old_writer(connection):
            try:
                await gate.wait()
            finally:
                # Model cleanup from a previous successful upload session.
                connection.uploading = False
                connection.upload_finished.set()

        old = {tid: asyncio.create_task(old_writer(c)) for tid, c in self.backend.trains.items()}
        self.backend.tasks.update(old)
        await asyncio.sleep(0)

        async def scan(trains, debug):
            order.append('scan')
            self.assertTrue(all(task.done() for task in old.values()))
            self.assertEqual([t['id'] for t in trains], ['a', 'b'])
            return devices, {}

        def run(connection):
            async def upload(*, source, device):
                tid = connection.train['id']
                self.assertTrue(connection.uploading)
                self.assertIs(device, devices[tid])
                self.assertEqual(source, 'source')
                order.append(tid)
                if tid == 'b':
                    self.assertFalse(self.backend.tasks['a'].done())
                connection.uploading = False
                connection.upload_status('Verified zero throttle', 'complete')
                await gate.wait()  # The successful motor-control session stays open.
            return upload

        for connection in self.backend.trains.values():
            connection.run = AsyncMock(side_effect=run(connection))
        try:
            with patch('train_controllers.pybricks_updates.scan_hubs', side_effect=scan) as scanner:
                self.backend.update_hubs('source')
                await asyncio.sleep(0)
                await asyncio.wait_for(self.backend.hub_update, 2)
                scanner.assert_awaited_once()
            self.assertEqual(order, ['scan', 'a', 'b'])
            self.assertTrue(all(not task.done() for task in self.backend.tasks.values()))
            self.assertTrue(all(not c.uploading for c in self.backend.trains.values()))
        finally:
            await self.backend.shutdown()

    async def test_failed_upload_continues_to_next_hub_with_traceback(self):
        a, b = self.backend.trains.values()
        a.run = AsyncMock(side_effect=OSError('connection failed'))
        async def success(**kwargs):
            b.uploading = False
            b.upload_status('Verified', 'complete')
            return False
        b.run = AsyncMock(side_effect=success)
        with patch('train_controllers.pybricks_updates.scan_hubs', new=AsyncMock(return_value=({'a': object(), 'b': object()}, {}))):
            self.backend.update_hubs('source')
            await asyncio.sleep(0)
            await asyncio.wait_for(self.backend.hub_update, 1)
        self.assertEqual(a.upload_phase, 'failed')
        self.assertEqual(b.upload_phase, 'complete')
        b.run.assert_awaited_once()
        events = list(self.backend.events.queue)
        self.assertTrue(any(e[0] == 'program_debug' and 'Traceback' in e[2] for e in events))
        await self.backend.shutdown()

    async def test_missing_hubs_are_reported_without_connecting_or_compiling(self):
        for connection in self.backend.trains.values():
            connection.run = AsyncMock()
        with patch('train_controllers.pybricks_updates.scan_hubs', new=AsyncMock(return_value=(
                {}, {'a': 'Hub a not found', 'b': 'Duplicate hub name b'}))), \
                patch('train_controllers.pybricks.compile_program') as compiler:
            self.backend.update_hubs('source')
            await asyncio.sleep(0)
            await asyncio.wait_for(self.backend.hub_update, 1)
        compiler.assert_not_called()
        for connection in self.backend.trains.values():
            connection.run.assert_not_called()
            self.assertEqual(connection.upload_phase, 'failed')
            self.assertFalse(connection.uploading)
        await self.backend.shutdown()

    async def test_shutdown_cancels_batch_scan_and_releases_all_targets(self):
        scanning = asyncio.Event()
        async def scan(*args):
            scanning.set()
            await asyncio.Event().wait()
        with patch('train_controllers.pybricks_updates.scan_hubs', side_effect=scan):
            self.backend.update_hubs('source')
            await asyncio.wait_for(scanning.wait(), 1)
            await self.backend.shutdown()
        self.assertTrue(self.backend.hub_update.cancelled())
        self.assertTrue(all(not c.uploading for c in self.backend.trains.values()))
        self.assertTrue(all(c.upload_phase == 'failed' for c in self.backend.trains.values()))
