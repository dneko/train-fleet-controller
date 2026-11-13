"""Optional recording, chart rendering, profile reset, and nonmodal advisories."""
import copy
import os
from pathlib import Path
import time
import unittest
from unittest.mock import Mock, patch

from battery_monitor import BatteryReading
from power_profile import PowerAlert, PowerSetpoints
from fleet_model import load_config
import test_external_ui


@unittest.skipUnless(os.environ.get('TRAINSTUFF_GUI_TESTS') == '1', 'Requires a desktop Tk session')
class BatteryHistoryUITests(unittest.TestCase):
    setUp = test_external_ui.ExternalUITests.setUp

    def live(self):
        self.app.demo = False
        self.app.demo_running = False
        self.app.backend.record_history = Mock()
        self.now = time.monotonic()

    def tick_at(self, at):
        with patch('fleet_ui.time.monotonic', return_value=at):
            self.app.poll()

    def test_collection_is_off_by_default_and_stopping_preserves_only_recorded_samples(self):
        self.live()
        self.app.events.put(('battery', 'a', BatteryReading(8000, 250, self.now)))
        self.tick_at(self.now)
        self.assertFalse(self.app.battery_recording)
        self.assertFalse(self.app.battery_histories)
        self.assertFalse(self.app.power_profiles)
        with patch('fleet_ui.time.monotonic', return_value=self.now+1):
            self.app.set_battery_recording(True)
            self.app.command('a', 40)
        self.app.events.put(('sent_throttle', 'a', self.now+1.1, 40))
        self.app.events.put(('battery', 'a', BatteryReading(8000, 250, self.now+2)))
        self.tick_at(self.now+2)
        history = self.app.battery_history_for('a')
        row = history.rows(self.now, self.now+3)[0]
        self.assertEqual((row.demand, row.sent, row.watts), (40, 40, 2))
        with patch('fleet_ui.time.monotonic', return_value=self.now+3):
            self.app.set_battery_recording(False)
        self.app.events.put(('battery', 'a', BatteryReading(8000, 500, self.now+4)))
        self.tick_at(self.now+4)
        self.assertEqual(len(history.samples), 1)
        self.assertEqual(self.app.batteries['a'].watts, 4)
        self.assertEqual(self.app.backend.record_history.call_args_list[-1].args, (False,))
        self.assertNotIn('battery_histories', self.app.config)

    def test_recording_choice_survives_restart_in_live_and_motor_modes_and_demo_preserves_it(self):
        import tkinter as tk
        from fleet_ui import FleetApp
        self.live()
        for enabled in (True, False):
            self.app.set_battery_recording(enabled)
            self.assertEqual(load_config(self.app.path)['battery_recording'], enabled)
            before = self.app.path.read_bytes()
            for demo, motor in ((False, False), (False, True), (True, False)):
                with self.subTest(enabled=enabled, demo=demo, motor=motor):
                    root = tk.Tk()
                    backend = Mock()
                    # Each simulated restart owns its own Tcl interpreter and
                    # must also own Tk's default variables/images.
                    with patch('tkinter._default_root', root):
                        reopened = None
                        try:
                            with patch('fleet_ui.load_reader_token', return_value=''):
                                reopened = FleetApp(root, load_config(self.app.path), self.app.path, demo,
                                                    lambda *args: backend, motor_only=motor)
                            self.assertEqual(reopened.battery_recording, enabled and not demo)
                            if enabled and not demo:
                                backend.record_history.assert_called_once_with(True)
                            else:
                                backend.record_history.assert_not_called()
                            if demo:
                                reopened.set_battery_recording(True)
                                self.assertFalse(reopened.battery_recording)
                            self.assertEqual(self.app.path.read_bytes(), before)
                        finally:
                            reopened.close() if reopened is not None else root.destroy()

    def test_loading_configuration_restores_its_recording_choice(self):
        self.live()
        self.app.set_battery_recording(True)
        updated = copy.deepcopy(self.app.config)
        updated['battery_recording'] = False
        self.app.events.put(('configured', updated))
        self.tick_at(self.now+1)
        self.assertFalse(self.app.battery_recording)
        self.app.backend.record_history.assert_called_with(False)
        updated = copy.deepcopy(updated)
        updated['battery_recording'] = True
        self.app.events.put(('configured', updated))
        self.tick_at(self.now+2)
        self.assertTrue(self.app.battery_recording)
        self.app.backend.record_history.assert_called_with(True)

    def test_failed_preference_save_keeps_checkbox_runtime_and_saved_choice_consistent(self):
        self.live()
        self.app.set_battery_recording(False)
        before = self.app.path.read_bytes()
        self.app.show_battery_history('a')
        dialog = self.app.battery_history_dialog
        with patch('fleet_ui.save_config', side_effect=OSError('disk full')), \
                patch('battery_history_ui.messagebox.showerror') as error:
            dialog.record_button.invoke()
        error.assert_called_once()
        self.assertFalse(dialog.recording.get())
        self.assertFalse(self.app.battery_recording)
        self.assertFalse(self.app.config['battery_recording'])
        self.assertEqual(self.app.path.read_bytes(), before)
        self.app.backend.record_history.assert_not_called()

    def test_charts_hover_export_and_reset_do_not_change_train_controls(self):
        self.live()
        with patch('fleet_ui.time.monotonic', return_value=self.now):
            self.app.set_battery_recording(True)
            self.app.command('a', 40)
        self.app.events.put(('sent_throttle', 'a', self.now+.1, 40))
        for at in (self.now+2, self.now+4):
            self.app.events.put(('battery', 'a', BatteryReading(8000, 250, at)))
        self.tick_at(self.now+4)
        with patch('fleet_ui.time.monotonic', return_value=self.now+5):
            self.app.set_battery_recording(False)
        self.app.show_battery_history('a')
        dialog = self.app.battery_history_dialog
        self.root.update()
        dialog.refresh()
        self.assertEqual(len(dialog.rows), 2)
        for metric in ('volts', 'amps', 'watts', 'demand', 'sent'):
            self.assertTrue(dialog.timeline.find_withtag(f'series-{metric}'), metric)
        self.assertTrue(dialog.scatter.find_withtag('current-sample'))
        left, top, right, bottom = dialog.plot_bounds
        x = left + (dialog.rows[-1].at-dialog.start)/(dialog.end-dialog.start)*(right-left)
        dialog.hover(type('Event', (), dict(x=x, y=top))())
        self.assertIn('Demand +40%', dialog.hover_text.get())
        destination = Path(self.folder.name)/'battery.csv'
        with patch('battery_history_ui.filedialog.asksaveasfilename', return_value=str(destination)):
            dialog.export()
        self.assertIn('sent_throttle_percent', destination.read_text())
        self.app.backend.speed.reset_mock()
        throttle = dict(self.app.supervisor.throttle)
        dialog.reset_button.invoke()
        self.assertFalse(dialog.rows)
        self.assertIsNone(self.app.battery_history_for('a'))
        self.assertIsNone(self.app.power_profile_for('a'))
        self.assertEqual(self.app.supervisor.throttle, throttle)
        self.app.backend.speed.assert_not_called()

    def test_advisories_are_informational_nonmodal_and_reset_is_per_train(self):
        self.live()
        self.app.set_battery_recording(True)
        other_history = self.app.battery_history_for('b')
        self.app.power_profile_for('a', create=True)
        self.app.command('a', 40)
        original = dict(self.app.supervisor.throttle)
        self.app.backend.speed.reset_mock()
        self.app.show_power_alert('a', PowerAlert('higher', 4, 2, 1.2, 2.8, 40))
        dialog = self.app.power_alert_dialogs['a']
        self.root.update()
        self.assertIsNone(self.root.grab_current())
        self.assertIn('pushing another train', dialog.advisory.get())
        self.assertEqual(self.app.supervisor.throttle, original)
        self.app.show_power_alert('a', PowerAlert('lower', .2, 2, 1.2, 2.8, 40))
        self.assertIs(dialog, self.app.power_alert_dialogs['a'])
        self.assertIn('pushing another train', dialog.advisory.get())
        self.assertEqual(self.app.power_alarm_for('a').direction, 'higher')
        self.app.reset_battery_data('a')
        self.assertFalse(dialog.winfo_exists())
        self.assertTrue(self.app.battery_recording)
        self.assertIs(self.app.battery_history_for('b'), other_history)
        self.assertIsNone(self.app.power_profile_for('a'))
        self.app.backend.speed.assert_not_called()

    def test_closing_graph_keeps_opt_in_recording_and_reopening_restores_controls(self):
        self.live()
        self.app.show_battery_history('a')
        dialog = self.app.battery_history_dialog
        dialog.record_button.invoke()
        self.assertTrue(self.app.battery_recording)
        dialog.alert_button.invoke()
        self.assertFalse(self.app.power_alerts_enabled)
        dialog.destroy()
        self.assertTrue(self.app.battery_recording)
        self.app.show_battery_history('a')
        self.assertTrue(self.app.battery_history_dialog.recording.get())
        self.assertFalse(self.app.battery_history_dialog.alerts.get())

    def test_demo_never_records_and_window_controls_fit(self):
        self.app.show_battery_history('a')
        dialog = self.app.battery_history_dialog
        dialog.geometry('760x660')
        self.root.update()
        self.assertTrue(dialog.record_button.instate(['disabled']))
        self.app.set_battery_recording(True)
        self.assertFalse(self.app.battery_recording)
        self.assertFalse(self.app.battery_histories)
        for widget in (dialog.record_button, dialog.export_button, dialog.choice, dialog.window,
                       dialog.reset_button, dialog.alert_button, dialog.apply_setpoints_button,
                       dialog.clear_alarm_button, dialog.view_alarm_button, *dialog.setpoint_entries):
            self.assertTrue(widget.winfo_viewable())
            self.assertLessEqual(widget.winfo_rootx()+widget.winfo_reqwidth(), dialog.winfo_rootx()+dialog.winfo_width())
            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_reqheight(), dialog.winfo_rooty()+dialog.winfo_height())

    def test_hub_change_starts_a_new_profile_but_name_edit_preserves_it(self):
        self.live()
        self.app.set_battery_recording(True)
        history = self.app.battery_history_for('a')
        profile = self.app.power_profile_for('a', create=True)
        edited = copy.deepcopy(self.app.config)
        edited['trains'][0]['name'] = 'Renamed train'
        self.app.apply_setup(edited)
        self.assertIs(self.app.battery_history_for('a'), history)
        self.assertIs(self.app.power_profile_for('a'), profile)
        edited = copy.deepcopy(self.app.config)
        edited['trains'][0]['hub_name'] = 'Different physical hub'
        self.app.apply_setup(edited)
        self.assertIsNone(self.app.battery_history_for('a'))
        self.assertIsNone(self.app.power_profile_for('a'))

    def test_recorded_telemetry_learns_then_raises_one_advisory_and_stops_when_disabled(self):
        self.live()
        with patch('fleet_ui.time.monotonic', return_value=self.now):
            self.app.set_battery_recording(True)
            self.app.command('a', 40)
        self.app.events.put(('sent_throttle', 'a', self.now+.1, 40))
        with patch.object(self.app, 'show_power_alert') as alert:
            for seconds in range(2, 50, 2):
                self.app.events.put(('battery', 'a', BatteryReading(8000, 250, self.now+seconds)))
                self.tick_at(self.now+seconds)
            alert.assert_not_called()
            profile = self.app.power_profile_for('a')
            self.assertIsNotNone(profile.expected(profile.band(40)))
            for seconds in (50, 52, 54, 56):
                self.app.events.put(('battery', 'a', BatteryReading(8000, 500, self.now+seconds)))
                self.tick_at(self.now+seconds)
            alert.assert_called_once()
            self.assertEqual(alert.call_args.args[1].direction, 'higher')
            with patch('fleet_ui.time.monotonic', return_value=self.now+57):
                self.app.set_battery_recording(False)
            samples = sum(len(band) for band in profile.bands.values())
            self.app.events.put(('battery', 'a', BatteryReading(8000, 0, self.now+58)))
            self.tick_at(self.now+58)
            self.assertEqual(sum(len(band) for band in profile.bands.values()), samples)
            alert.assert_called_once()


    def test_voltage_drop_is_ignored_but_current_rise_at_same_watts_latches_in_amps(self):
        self.live()
        with patch('fleet_ui.time.monotonic', return_value=self.now):
            self.app.set_battery_recording(True)
            self.app.command('a', 40)
        self.app.events.put(('sent_throttle', 'a', self.now+.1, 40))
        for seconds in range(2, 50, 2):
            self.app.events.put(('battery', 'a', BatteryReading(8000, 250, self.now+seconds)))
            self.tick_at(self.now+seconds)
        for seconds in (50, 52, 54, 56):
            self.app.events.put(('battery', 'a', BatteryReading(6000, 250, self.now+seconds)))
            self.tick_at(self.now+seconds)
        self.assertIsNone(self.app.power_alarm_for('a'))
        profile = self.app.power_profile_for('a')
        self.assertEqual(profile.expected(profile.band(40)), (.25, .225, .275))
        with patch('fleet_ui.time.monotonic', return_value=self.now+56):
            self.app.show_battery_history('a')
        graph = self.app.battery_history_dialog
        self.assertEqual(graph.tabs.tab(graph.scatter, 'text'), 'Current vs demand')
        # Identical amps/demand yield a single scatter point despite differing watts.
        self.assertEqual(len(graph.scatter.find_withtag('current-sample')), 1)
        self.assertTrue(graph.scatter.find_withtag('expected-current'))
        for seconds in (58, 60, 62):
            # 5 V * 0.4 A = the original 2 W, but current is 60% higher.
            self.app.events.put(('battery', 'a', BatteryReading(5000, 400, self.now+seconds)))
            self.tick_at(self.now+seconds)
        alarm = self.app.power_alarm_for('a')
        self.assertEqual(alarm.metric, 'amps')
        self.assertEqual((alarm.measured, alarm.expected), (.4, .25))
        self.assertIn('0.400 A', self.app.power_alert_dialogs['a'].advisory.get())
        self.assertIn('0.250 A', self.app.battery_details('a'))
        self.assertEqual(load_config(self.app.path)['trains'][0]['power_alarm']['metric'], 'amps')
        # All original energy/power history remains accurate and exportable.
        rows = self.app.battery_history_for('a').rows(self.now, self.now+62)
        self.assertEqual((rows[-1].volts, rows[-1].amps, rows[-1].watts), (5, .4, 2))

    def test_legacy_watt_alarm_survives_loading_until_manually_cleared(self):
        legacy = dict(direction='higher', measured=2.4, expected=2, lower=1.8, upper=2.2,
                      demand=40, confirm_samples=3)
        self.app.config['trains'][0]['power_alarm'] = legacy
        from fleet_setup import save_config
        save_config(self.app.path, self.app.config)
        self.app.config = load_config(self.app.path)
        self.app.build()
        self.app.show_power_alert('a')
        text = self.app.power_alert_dialogs['a'].advisory.get()
        self.assertIn('2.40 W', text)
        self.assertIn('before current-based monitoring', text)
        self.assertNotIn('2.400 A', text)
        self.assertTrue(self.app.power_profile_for('a', create=True).latched)
        self.app.show_battery_history('a')
        self.assertIn('POWER ALARM', self.app.battery_history_dialog.alarm_status.get())
        self.app.battery_history_dialog.clear_alarm_button.invoke()
        self.assertIsNone(self.app.power_alarm_for('a'))
        self.assertNotIn('power_alarm', load_config(self.app.path)['trains'][0])

    def test_setpoints_save_per_train_without_stopping_or_erasing_learning_and_reset_keeps_them(self):
        self.live()
        self.app.set_battery_recording(True)
        self.app.command('a', 40)
        profile = self.app.power_profile_for('a', create=True)
        profile.bands[profile.band(40)] = [2.] * 12
        self.app.show_battery_history('a')
        dialog = self.app.battery_history_dialog
        self.app.backend.speed.reset_mock()
        dialog.high_percent.set('5')
        dialog.low_percent.set('12')
        dialog.confirm_samples.set('2')
        dialog.refresh()  # Live refresh must not overwrite edits.
        self.assertEqual(dialog.high_percent.get(), '5')
        dialog.apply_setpoints_button.invoke()
        self.assertEqual(self.app.power_setpoints_for('a'), PowerSetpoints(5, 12, 2))
        self.assertEqual(self.app.power_setpoints_for('b'), PowerSetpoints())
        self.assertEqual(load_config(self.app.path)['trains'][0]['power_setpoints'], PowerSetpoints(5, 12, 2).to_config())
        self.assertIs(self.app.power_profile_for('a'), profile)
        self.assertEqual(profile.expected(profile.band(40)), (2, 1.76, 2.1))
        self.assertEqual(self.app.supervisor.throttle['a'], 40)
        self.app.backend.speed.assert_not_called()
        dialog.select_train('b')
        self.assertEqual(dialog.high_percent.get(), '10')
        dialog.select_train('a')
        self.assertEqual(dialog.high_percent.get(), '5')
        dialog.reset_button.invoke()
        self.assertEqual(self.app.power_setpoints_for('a'), PowerSetpoints(5, 12, 2))

    def test_invalid_setpoints_leave_applied_values_and_file_unchanged(self):
        self.live()
        self.app.set_power_setpoints('a', PowerSetpoints().to_config())
        original = self.app.path.read_bytes()
        self.app.show_battery_history('a')
        dialog = self.app.battery_history_dialog
        for high, low, count in (('nan', '10', '3'), ('10', '110', '3'), ('10', '10', '1.5')):
            dialog.high_percent.set(high)
            dialog.low_percent.set(low)
            dialog.confirm_samples.set(count)
            with patch('battery_history_ui.messagebox.showerror') as error:
                dialog.apply_setpoints_button.invoke()
            error.assert_called_once()
            self.assertEqual(self.app.power_setpoints_for('a'), PowerSetpoints())
            self.assertEqual(self.app.path.read_bytes(), original)

    def test_alarm_survives_normal_readings_recording_changes_settings_and_rebuild_until_cleared(self):
        self.live()
        self.app.set_battery_recording(True)
        self.app.command('a', 40)
        profile = self.app.power_profile_for('a', create=True)
        self.app.show_power_alert('a', PowerAlert('higher', 2.4, 2, 1.8, 2.2, 40))
        self.assertTrue(profile.latched)
        dialog = self.app.power_alert_dialogs['a']
        dialog.destroy()  # Closing a popup only hides it.
        self.app.set_battery_recording(False)
        self.app.set_power_alerts(False)
        self.app.set_power_setpoints('a', PowerSetpoints(30, 30, 1).to_config())
        self.assertTrue(profile.latched)
        self.assertIsNotNone(self.app.power_alarm_for('a'))
        self.assertIn('CURRENT ALARM', self.app.battery_labels['a']['text'])
        saved = load_config(self.app.path)
        self.assertIn('power_alarm', saved['trains'][0])
        self.app.config = saved
        self.app.power_profiles.clear()
        self.app.build()  # Loading saved configuration restores the latch.
        self.assertTrue(self.app.power_profile_for('a', create=True).latched)
        self.app.show_battery_history('a')
        history_dialog = self.app.battery_history_dialog
        self.assertIn('LATCHED', history_dialog.alarm_status.get())
        self.assertFalse(history_dialog.clear_alarm_button.instate(['disabled']))
        self.app.backend.speed.reset_mock()
        history_dialog.clear_alarm_button.invoke()
        self.assertIsNone(self.app.power_alarm_for('a'))
        self.assertNotIn('power_alarm', load_config(self.app.path)['trains'][0])
        self.assertFalse(self.app.power_profile_for('a').latched)
        self.assertNotIn('CURRENT ALARM', self.app.battery_labels['a']['text'])
        self.app.backend.speed.assert_not_called()
