from contextlib import ExitStack
"""Mixed controller types through real Tk; no physical outputs."""
import copy
import os
import unittest
from unittest.mock import Mock, patch

from fleet_model import load_config
from power_profile import PowerAlert
from controller_fakes import output_train, registered_outputs
import test_external_ui


@unittest.skipUnless(os.environ.get('TRAINSTUFF_GUI_TESTS') == '1', 'Requires a desktop Tk session')
class ControllerUITests(unittest.TestCase):
    def setUp(self):
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        contexts.enter_context(registered_outputs())
        test_external_ui.ExternalUITests.setUp(self)
        self.app.backend.update_trains = Mock()

    def add_output(self):
        self.app.config['trains'].append(output_train())
        self.app.build()
        self.app.backend.speed = Mock()
        self.app.backend.update_hubs = Mock()
        self.app.backend.record_history = Mock()

    def test_driver_selection_and_settings_are_staged_and_saved_without_stopping_other_train(self):
        self.app.command('a', 20)
        self.app.command('b', 25)
        self.app.backend.speed.reset_mock()
        self.app.setup('a')
        dialog = self.app.setup_dialog
        fields = dialog.pages['trains'][1]
        self.assertEqual(fields['controller_type'].get(), 'Pybricks City Hub')
        fields['controller_type'].set('Test track output')
        dialog.controller_settings('trains')
        settings = dialog.controller_dialog
        settings.variables['port'].set('test-port')
        settings.variables['channel'].set('2')
        settings.apply()
        self.assertFalse(settings.winfo_exists())
        self.assertEqual(self.root.grab_current(), dialog)
        self.assertNotIn('controller_type', self.app.config['trains'][0])
        self.assertTrue(dialog.controller_widgets['trains'][2].instate(['disabled']))
        dialog.apply()
        self.assertFalse(dialog.winfo_exists())
        saved = load_config(self.app.path)['trains'][0]
        self.assertEqual(saved['controller_type'], 'test-output')
        self.assertEqual(saved['controller_options'], dict(port='test-port', channel='2'))
        self.assertEqual(self.app.supervisor.throttle['a'], 0)
        self.assertEqual(self.app.supervisor.throttle['b'], 25)
        self.assertEqual([call.args for call in self.app.backend.speed.call_args_list], [('a', 0)])
        self.app.backend.update_trains.assert_called_once()

    def test_driver_settings_cancel_does_not_save_edits(self):
        self.add_output()
        before = copy.deepcopy(self.app.config)
        self.app.setup('track')
        dialog = self.app.setup_dialog
        dialog.controller_settings('trains')
        dialog.controller_dialog.variables['port'].set('changed')
        dialog.controller_dialog.destroy()
        self.assertEqual(self.root.grab_current(), dialog)
        self.assertEqual(dialog.config_data['trains'], before['trains'])
        dialog.destroy()
        self.assertEqual(self.app.config, before)

    def test_no_hub_controller_has_controls_but_no_hub_upload_or_telemetry(self):
        self.add_output()
        self.app.demo_running = True
        self.app.command('track', -30)
        self.assertEqual(self.app.supervisor.throttle['track'], -30)
        self.app.demo = False
        self.app.refresh_batteries()
        self.assertEqual(self.app.battery_labels['track']['text'], 'Power telemetry unavailable')
        self.assertIn('does not provide', self.app.battery_details('track'))
        self.app.show_battery_history()
        self.assertNotIn('track', self.app.battery_history_dialog.choices.values())
        self.app.set_battery_recording(True)
        self.assertIsNone(self.app.battery_history_for('track', create=True))
        self.assertIsNone(self.app.power_profile_for('track', create=True))
        self.app.show_hub_program()
        self.assertEqual({t['id'] for t in self.app.program_dialog.trains.values()}, {'a', 'b'})
        with self.assertRaises(ValueError):
            self.app.upload_hub_program('track', '', 'source')
        self.app.backend.speed.reset_mock()
        self.app.upload_active_hubs(list(self.app.program_dialog.trains.values()), 'source')
        self.assertEqual(self.app.supervisor.throttle['track'], -30)
        self.assertEqual(self.app.program_uploads, {'a', 'b'})
        self.assertTrue(all(c.args[0] != 'track' for c in self.app.backend.speed.call_args_list))

    def test_controller_change_keeps_latched_alarm_until_manual_reset(self):
        self.app.show_power_alert('a', PowerAlert('higher', 4, 2, 1.8, 2.2, 40))
        edited = copy.deepcopy(self.app.config)
        edited['trains'][0].update(controller_type='test-output', controller_options=dict(port='p', channel='1'))
        self.app.apply_setup(edited)
        self.assertIsNotNone(self.app.power_alarm_for('a'))
        self.app.demo = False
        self.app.backend.record_history = Mock()
        self.app.set_battery_recording(True)
        self.app.show_battery_history('a')
        self.assertIn('a', self.app.battery_history_dialog.choices.values())
        self.app.reset_battery_data('a')
        self.assertIsNone(self.app.power_alarm_for('a'))
        self.assertIsNone(self.app.power_profile_for('a'))
