import io
from pathlib import Path
import runpy
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from battery_monitor import BatteryReading, BATTERY_STALE_SECONDS
from fleet_controller import TrainConnection


class BatteryProtocolTests(unittest.TestCase):
    def test_every_packet_split_preserves_battery_and_ready(self):
        payload = b'rdy\r\nbattery 8120 145\r\nrdy\nbattery 7990 620\n'
        for split in range(len(payload) + 1):
            events = []
            train = TrainConnection({'id': 'a'}, events.append)
            train.receive(None, b'\x01' + payload[:split])
            train.receive(None, b'\x01' + payload[split:])
            self.assertTrue(train.ready.is_set(), split)
            self.assertEqual([(e[1], e[2].voltage_mv, e[2].current_ma) for e in events],
                             [('a', 8120, 145), ('a', 7990, 620)], split)
            self.assertEqual(train.rx, b'')

    def test_battery_does_not_grant_ready_or_clear_timeout_stop(self):
        events = []
        train = TrainConnection({'id': 'a'}, events.append)
        train.accepting, train.last_speed, train.pending = True, 40, 60
        train.ready.set()
        for char in b'timeout\nbattery 7100 0\n':
            train.receive(None, bytes((1, char)))
        self.assertFalse(train.ready.is_set())
        self.assertFalse(train.accepting)
        self.assertEqual(train.last_speed, 0)
        self.assertEqual(train.pending, 0)
        self.assertTrue(train.stop_pending)
        self.assertEqual(events[0], ('ready', 'a', False))
        train.receive(None, b'\x01rdy\n')
        self.assertTrue(train.ready.is_set())
        self.assertTrue(train.stop_pending)

    def test_bad_oversized_and_non_stdout_data_are_ignored(self):
        events = []
        train = TrainConnection({'id': 'a'}, events.append)
        train.receive(None, b'\x00battery 8000 123\nrdy\n')
        for line in (b'battery 0 1', b'battery 8000 -2', b'battery 99999 0',
                     b'battery 8000 99999', b'battery 8.0 5', b'battery 8000',
                     b'battery 8000 5 extra', b'battery \xff 0',
                     b'battery rdy timeout', b'not rdy', b'not timeout'):
            train.receive(None, b'\x01' + line + b'\n')
        # Ignore the entire overlong line, including a valid-looking tail.
        train.receive(None, b'\x01' + b'x' * 300)
        self.assertLessEqual(len(train.rx), 256)
        train.receive(None, b'\x01rdy\n')
        self.assertFalse(train.ready.is_set())
        self.assertTrue(all(e[0] == 'hub_output' for e in events))
        events.clear()
        train.receive(None, b'\x01battery 8000 123\nrdy\n')
        self.assertTrue(train.ready.is_set())
        self.assertEqual(len(events), 1)

    def test_startup_exception_is_visible_and_motion_is_cleared(self):
        events = []
        train = TrainConnection({'id': 'a'}, events.append)
        train.accepting = True
        train.last_speed, train.pending = 40, 60
        train.ready.set()
        train.receive(None, b'\x01Traceback (most recent call last):\r\n  File "main.py", line 10\r\nOSE')
        train.receive(None, b'\x01rror: [Errno 19] ENODEV\r\n')
        self.assertFalse(train.ready.is_set())
        self.assertFalse(train.accepting)
        self.assertEqual(train.pending, 0)
        self.assertEqual(train.last_speed, 0)
        self.assertTrue(train.stop_pending)
        self.assertIn('OSError: [Errno 19] ENODEV', train.waiting_status())
        self.assertIn(('hub_output', 'a', 'OSError: [Errno 19] ENODEV'), events)
        # Restarting successfully clears the error, but still requires zero first.
        train.receive(None, b'\x01rdy\n')
        self.assertIsNone(train.hub_error)
        self.assertTrue(train.stop_pending)
        self.assertFalse(train.accepting)

    def test_hubs_keep_independent_partial_lines(self):
        events = []
        a = TrainConnection({'id': 'a'}, events.append)
        b = TrainConnection({'id': 'b'}, events.append)
        a.receive(None, b'\x01battery 81')
        b.receive(None, b'\x01battery 7500 25\n')
        a.receive(None, b'\x0120 140\n')
        self.assertEqual([(e[1], e[2].voltage_mv) for e in events], [('b', 7500), ('a', 8120)])

    def test_reading_age_and_stale_presentation(self):
        with patch('battery_monitor.time.monotonic', return_value=100):
            reading = BatteryReading.from_line(b'battery 8120 145')
        self.assertEqual(reading.card_text(105), '8.12 V · 0.145 A · 1.18 W')
        self.assertEqual(reading.card_text(100 + BATTERY_STALE_SECONDS), '8.12 V · 0.145 A · 1.18 W · stale')
        self.assertIn('145 mA', reading.details(107))
        self.assertIn('7s ago — stale', reading.details(107))
        self.assertEqual(reading.age(99), 0)

    def test_warning_boundaries_and_calculated_power(self):
        for mv, level in ((6000, ''), (5999, 'low'), (5500, 'low'), (5499, 'critical')):
            reading = BatteryReading(mv, 250, 100)
            self.assertEqual(reading.warning_level, level)
            self.assertAlmostEqual(reading.watts, mv / 1000 * .25)
            self.assertIn('0.250 A', reading.card_text(100))
            if level:
                self.assertTrue(reading.warning(100))
                self.assertTrue(reading.warning(107).startswith('Last reading:'))
        self.assertIn('imminent', BatteryReading(5400, 0, 100).warning(100))


class BatteryConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_and_reconnect_clear_reading_and_partial_input(self):
        events = []
        train = TrainConnection({'id': 'a', 'hub_name': 'Train A'}, events.append)

        class Client:
            def __init__(self, device):
                self.is_connected = True

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def start_notify(self, uuid, callback):
                callback(None, b'\x01battery 8120 145\nrdy\n')

            async def write_gatt_char(self, uuid, data, response):
                self.data = data
                self.is_connected = False

        async def scan(name, timeout):
            return object()

        bleak = SimpleNamespace(BleakClient=Client, BleakScanner=SimpleNamespace(find_device_by_name=scan))
        with patch.dict(sys.modules, {'bleak': bleak}):
            for _ in range(2):
                events.clear()
                train.rx = b'old partial line'
                train.rx_discarding = True
                await train.run()
                battery_events = [e[2] for e in events if e[0] == 'battery']
                self.assertIsNone(battery_events[0])
                self.assertEqual(battery_events[1].voltage_mv, 8120)
                self.assertIsNone(battery_events[2])
                self.assertEqual(train.client.data, b'\x06s0\n')
                self.assertFalse(train.ready.is_set())
                self.assertFalse(train.accepting)


class HubBatteryTests(unittest.TestCase):
    def simulate(self, fail_read=False, inputs=None, lights=None):
        now = [0]
        motor_calls, battery_calls = [], []
        inputs = [list(row) for row in (inputs or [[0, 's0\ns40\n'], [2500, 's40\n'], [3000, 's0\ns30\n'], [4200, 'bye\n']])]
        if lights is None:
            lights = []

        class Stopwatch:
            def __init__(self):
                self.start = now[0]

            def time(self):
                return now[0] - self.start

            def reset(self):
                self.start = now[0]

        class Motor:
            def __init__(self, port):
                pass

            def stop(self):
                motor_calls.append((now[0], 'stop'))

            def dc(self, speed):
                motor_calls.append((now[0], speed))

        class Poll:
            def register(self, stream):
                pass

            def poll(self, timeout):
                return bool(inputs and now[0] >= inputs[0][0])

        def read(size):
            char = inputs[0][1][0]
            inputs[0][1] = inputs[0][1][1:]
            if not inputs[0][1]:
                inputs.pop(0)
            return char

        def voltage():
            battery_calls.append(now[0])
            if fail_read and now[0] == 2000:
                raise OSError('sensor temporarily unavailable')
            return 8120

        def wait(ms):
            now[0] += ms
            if now[0] > 4500:
                raise AssertionError('Hub did not exit on bye')

        hub = SimpleNamespace(battery=SimpleNamespace(voltage=voltage, current=lambda: 145),
                              light=SimpleNamespace(on=lambda color: lights.append((now[0], 'on', color)),
                                                    blink=lambda color, durations: lights.append((now[0], 'blink', color))))
        values = {
            'pybricks': {},
            'pybricks.pupdevices': {'DCMotor': Motor},
            'pybricks.parameters': {'Port': SimpleNamespace(A='A'),
                                    'Color': SimpleNamespace(YELLOW=1, RED=2, GREEN=3, WHITE=4)},
            'pybricks.hubs': {'CityHub': lambda: hub},
            'pybricks.tools': {'wait': wait, 'StopWatch': Stopwatch},
            'usys': {'stdin': SimpleNamespace(read=read)},
            'uselect': {'poll': Poll},
        }
        modules = {}
        for name, attrs in values.items():
            modules[name] = ModuleType(name)
            modules[name].__dict__.update(attrs)
        stdout = io.StringIO()
        with patch.dict(sys.modules, modules), patch('sys.stdout', stdout):
            runpy.run_path(str(Path(__file__).resolve().parents[1] / 'firmware' / 'city_hub.py'))
        return motor_calls, battery_calls, stdout.getvalue().splitlines()

    def test_telemetry_runs_while_stopped_without_feeding_watchdog(self):
        motors, reports, lines = self.simulate()
        self.assertEqual(reports, [0, 2000, 4000])
        self.assertEqual(lines.count('battery 8120 145'), 3)
        self.assertEqual(lines.count('timeout'), 1)
        self.assertTrue(any(2000 < t < 2100 and value == 'stop' for t, value in motors))
        self.assertEqual([value for _, value in motors if isinstance(value, int)], [0, 40, 0, 30])
        self.assertEqual(motors[-1][1], 'stop')

    def test_temporary_battery_error_keeps_motor_control_and_next_report_working(self):
        motors, reports, lines = self.simulate(fail_read=True)
        self.assertEqual(reports, [0, 2000, 4000])
        self.assertEqual(lines.count('battery 8120 145'), 2)
        self.assertIn('timeout', lines)
        self.assertEqual([value for _, value in motors if isinstance(value, int)], [0, 40, 0, 30])

    def test_waiting_blinks_without_heartbeat_restart_and_resumes_green(self):
        lights = []
        motors, _, lines = self.simulate(lights=lights, inputs=[
            [0, 's0w1\n'], [500, 's0w1\n'], [1000, 's20\n'],
            [1700, 's0w1\n'], [2000, 's0w1\n'], [4200, 'bye\n']])
        self.assertEqual(len([event for event in lights if event[1]=='blink']),2)
        self.assertTrue(any(1000 <= at < 1100 and kind=='on' and color==3 for at,kind,color in lights))
        self.assertEqual([value for _,value in motors if isinstance(value,int)], [0,0,20,0,0])
        self.assertIn('features waiting-led',lines)
        self.assertIn('timeout',lines)
        self.assertTrue(any(at > 4000 and kind=='on' and color==2 for at,kind,color in lights))

    def test_waiting_command_cannot_request_motion(self):
        motors, _, _ = self.simulate(inputs=[[0,'s0\ns20w1\n'],[4200,'bye\n']])
        self.assertEqual([value for _,value in motors if isinstance(value,int)],[0])


if __name__ == '__main__':
    unittest.main()
