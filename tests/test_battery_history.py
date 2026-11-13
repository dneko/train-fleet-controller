import csv
from pathlib import Path
import tempfile
import unittest

from battery_history import BatteryHistory, HistoryRow, HISTORY_SECONDS
from battery_monitor import BatteryReading
from fleet_controller import TrainConnection
from power_profile import PowerAlert, PowerProfile, PowerSetpoints, MIN_SAMPLES


class BatteryHistoryTests(unittest.TestCase):
    def setUp(self):
        self.history = BatteryHistory(clock=lambda: 100, wall_clock=lambda: 1000)

    def test_commands_align_by_sample_time_and_signed_throttle(self):
        self.history.command('demand', 40, 100)
        self.history.command('demand', -20, 103)
        self.history.command('sent', 40, 101)
        self.history.command('sent', -20, 104)
        for at in (100, 102, 103, 105):
            self.history.add(BatteryReading(8000, 250, at))
        rows = self.history.rows(100, 105)
        self.assertEqual([(r.demand, r.sent) for r in rows], [(40, None), (40, 40), (-20, 40), (-20, -20)])
        self.assertTrue(all(r.volts == 8 and r.amps == .25 and r.watts == 2 for r in rows))

    def test_usage_integrates_measured_intervals_but_never_recording_or_stale_gaps(self):
        for at in (100, 102, 104):
            self.history.add(BatteryReading(8000, 250, at))
        self.history.gap(105)
        for at in (106, 108, 130, 132):
            self.history.add(BatteryReading(8000, 500, at))
        summary = self.history.summary(self.history.rows(100, 132))
        self.assertEqual(summary['seconds'], 8)
        self.assertAlmostEqual(summary['wh'], 24/3600)
        self.assertAlmostEqual(summary['mah'], 3/3.6)
        self.assertEqual(summary['peak'], 4)

    def test_retention_preserves_left_edge_command_and_bounds_memory(self):
        self.history.command('demand', 35, 0)
        self.history.command('demand', 40, 2)
        self.history.add(BatteryReading(8000, 250, 2))
        self.history.add(BatteryReading(8000, 250, HISTORY_SECONDS+3))
        self.assertEqual(len(self.history.samples), 1)
        self.assertEqual(self.history.rows(0, HISTORY_SECONDS+4)[0].demand, 40)
        self.assertLessEqual(len(self.history.demand), 2)

    def test_reset_boundary_rejects_queued_readings_and_commands(self):
        self.history.accept_after = 100
        self.assertFalse(self.history.add(BatteryReading(8000, 250, 99)))
        self.history.command('sent', 70, 99)
        self.assertFalse(self.history.sent)
        self.assertTrue(self.history.add(BatteryReading(8000, 250, 101)))
        self.assertFalse(self.history.add(BatteryReading(8000, 250, 101)))

    def test_csv_contains_real_units_unknown_sent_and_gap_identity(self):
        self.history.command('demand', 20, 100)
        self.history.add(BatteryReading(8000, 250, 100))
        self.history.gap(101)
        self.history.command('sent', 20, 102)
        self.history.add(BatteryReading(8000, 500, 102))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'history.csv'
            self.assertEqual(self.history.export_csv(path, 100, 102), 2)
            with path.open() as source:
                rows = list(csv.DictReader(source))
        self.assertEqual(rows[0]['sent_throttle_percent'], '')
        self.assertEqual(rows[1]['watts'], '4.0')
        self.assertEqual(rows[1]['controller_demand_percent'], '20')
        self.assertNotEqual(rows[0]['recording_segment'], rows[1]['recording_segment'])
        self.assertTrue(rows[0]['timestamp_utc'].endswith('+00:00'))

    def test_backend_command_telemetry_is_opt_in_and_forward_relative(self):
        events = []
        train = TrainConnection(dict(id='a', forward_sign=-1), events.append)
        train.record_sent_throttle(-30)
        self.assertEqual(events, [])
        train.recording_history = True
        train.record_sent_throttle(-30)
        self.assertEqual(events[-1][0], 'sent_throttle')
        self.assertEqual(events[-1][-1], 30)
        train.recording_history = False
        train.record_sent_throttle(0)
        self.assertEqual(len(events), 1)


class PowerProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile = PowerProfile()
        self.at = 0

    def sample(self, amps=2, demand=40, sent=40, segment=0, stable=True, alerting=True, volts=8):
        row = HistoryRow(self.at, volts, amps, volts*amps, demand, sent, segment)
        self.at += 2
        return self.profile.observe(row, stable, alerting)

    def learn(self):
        for _ in range(MIN_SAMPLES+3):
            self.assertIsNone(self.sample())
        self.assertIsNotNone(self.profile.expected(self.profile.band(40)))

    def test_high_and_low_draw_need_sustained_deviation_and_do_not_pollute_profile(self):
        for amps, direction in ((4, 'higher'), (.2, 'lower')):
            self.setUp()
            self.learn()
            baseline = list(self.profile.bands[self.profile.band(40)])
            self.assertIsNone(self.sample(amps))
            self.assertIsNone(self.sample(amps))
            alert = self.sample(amps)
            self.assertEqual(alert.direction, direction)
            self.assertEqual(alert.expected, 2)
            for _ in range(35):
                self.assertIsNone(self.sample(amps))
            self.assertEqual(list(self.profile.bands[self.profile.band(40)]), baseline)

    def test_short_spike_stop_acceleration_and_lost_connection_do_not_alert(self):
        self.learn()
        self.assertIsNone(self.sample(4))
        self.assertIsNone(self.sample(2))
        for _ in range(5):
            self.assertIsNone(self.sample(.1, demand=0, sent=0))
            self.assertIsNone(self.sample(.1, sent=None))
            self.assertIsNone(self.sample(4, stable=False))
        self.at += 10
        self.assertIsNone(self.sample(4))

    def test_directions_and_demand_bands_learn_separately(self):
        self.learn()
        for _ in range(8):
            self.assertIsNone(self.sample(.2, demand=-40, sent=-40))
        self.assertIsNone(self.profile.expected(self.profile.band(-40)))
        self.assertIsNone(self.profile.expected(self.profile.band(70)))

    def test_disabled_advisories_do_not_latch_or_consume_cooldown(self):
        self.learn()
        for _ in range(8):
            self.assertIsNone(self.sample(4, alerting=False))
        self.assertFalse(self.profile.latched)
        self.assertEqual(self.sample(4).direction, 'higher')

    def test_alarm_stays_latched_until_manual_clear(self):
        self.learn()
        for _ in range(2):
            self.sample(4)
        self.assertIsNotNone(self.sample(4))
        for _ in range(31):
            self.sample(2)
        self.assertTrue(self.profile.latched)
        self.profile.break_observation()
        self.profile.configure(PowerSetpoints(5, 5, 3))
        self.assertTrue(self.profile.latched)
        self.assertIsNone(self.sample(.2, segment=1))
        self.assertIsNone(self.sample(.2, demand=0, sent=0))
        self.assertIsNone(self.sample(.2, sent=None))
        self.assertIsNone(self.sample(.2, alerting=False))
        self.assertTrue(self.profile.latched)
        self.profile.clear_alarm()
        self.assertFalse(self.profile.latched)
        for _ in range(3):
            self.assertIsNone(self.sample(2))
        self.sample(.2)
        self.sample(.2)
        self.assertEqual(self.sample(.2).direction, 'lower')

    def test_default_limits_are_exactly_ten_percent_even_for_small_or_variable_loads(self):
        self.learn()
        self.assertEqual(self.profile.expected(self.profile.band(40)), (2, 1.8, 2.2))
        samples = self.profile.bands[self.profile.band(40)]
        samples.clear()
        samples.extend([.1, .3]*6)
        center, low, high = self.profile.expected(self.profile.band(40))
        self.assertAlmostEqual(center, .2)
        self.assertAlmostEqual(low, .18)
        self.assertAlmostEqual(high, .22)

    def test_small_percentage_changes_trigger_using_independent_setpoints_and_count(self):
        for amps, direction in ((2.12, 'higher'), (1.68, 'lower')):
            self.setUp()
            self.learn()
            self.profile.configure(PowerSetpoints(high_percent=5, low_percent=15, confirm_samples=2))
            self.assertEqual(self.profile.expected(self.profile.band(40)), (2, 1.7, 2.1))
            for _ in range(3):
                self.assertIsNone(self.sample())
            self.assertIsNone(self.sample(amps))
            alert = self.sample(amps)
            self.assertEqual(alert.direction, direction)
            self.assertEqual(alert.confirm_samples, 2)

    def test_configuring_limits_preserves_learning_and_does_not_clear_latch(self):
        self.learn()
        baseline = list(self.profile.bands[self.profile.band(40)])
        self.profile.latched = True
        self.profile.configure(PowerSetpoints(5, 20, 1))
        self.assertEqual(list(self.profile.bands[self.profile.band(40)]), baseline)
        self.assertTrue(self.profile.latched)
        self.assertEqual(self.profile.expected(self.profile.band(40)), (2, 1.6, 2.1))

    def test_voltage_changes_alone_do_not_change_baseline_or_trigger_load_alarm(self):
        self.learn()
        for volts in (9, 8, 7, 6, 5, 4, 9, 5):
            for _ in range(4):
                self.assertIsNone(self.sample(amps=2, volts=volts))
        self.assertEqual(self.profile.expected(self.profile.band(40)), (2, 1.8, 2.2))
        self.assertFalse(self.profile.latched)
        self.assertIn('2.000 A', self.profile.status)
        self.assertNotIn(' W', self.profile.status)

    def test_current_changes_trigger_even_when_watts_are_constant(self):
        for amps, volts, direction in ((4, 4, 'higher'), (1, 16, 'lower')):
            with self.subTest(direction=direction):
                self.setUp()
                self.learn()  # 2 A at 8 V = 16 W; new samples are also 16 W.
                self.assertIsNone(self.sample(amps=amps, volts=volts))
                self.assertIsNone(self.sample(amps=amps, volts=volts))
                alert = self.sample(amps=amps, volts=volts)
                self.assertEqual(alert.direction, direction)
                self.assertEqual(alert.metric, 'amps')
                self.assertEqual(alert.measured, amps)
                self.assertEqual(alert.expected, 2)
                self.assertEqual(alert.formatted(alert.expected), '2.000 A')

    def test_old_saved_alarm_retains_watts_and_new_alarm_requires_valid_metric(self):
        legacy = dict(direction='higher', measured=2.4, expected=2, lower=1.8, upper=2.2,
                      demand=40, confirm_samples=3)
        alarm = PowerAlert.from_config(legacy)
        self.assertEqual(alarm.metric, 'watts')
        self.assertEqual(alarm.formatted(alarm.measured), '2.40 W')
        self.assertEqual(alarm.label, 'POWER ALARM')
        self.assertEqual(PowerAlert.from_config(alarm.to_config()), alarm)
        with self.assertRaisesRegex(ValueError, 'metric'):
            PowerAlert.from_config(dict(legacy, metric='volts'))

    def test_setpoints_and_latched_alarm_round_trip_and_validate(self):
        from fleet_model import load_config
        from fleet_setup import save_config
        values = PowerSetpoints(5, 12, 2).to_config()
        alarm = PowerAlert('higher', 2.2, 2, 1.76, 2.1, 40, 2)
        config = dict(trains=[dict(id='a', name='A', hub_name='A', tags=[],
                                  power_setpoints=values, power_alarm=alarm.to_config())], readers=[])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'fleet.json'
            save_config(path, config)
            restored = load_config(path)['trains'][0]
        self.assertEqual(PowerSetpoints.from_config(restored['power_setpoints']), PowerSetpoints(5, 12, 2))
        self.assertEqual(PowerAlert.from_config(restored['power_alarm']), alarm)
        for invalid in (dict(high_percent=0), dict(low_percent=101), dict(high_percent=float('nan')),
                        dict(low_percent=float('inf')), dict(confirm_samples=1.5), dict(confirm_samples=True)):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                PowerSetpoints.from_config(invalid)
