"""Exercise saved/active membership through real Tk dialogs without hardware."""
import copy
import os
import unittest
from unittest.mock import Mock, patch

from fleet_model import load_config
from fleet_setup import move_train
import test_external_ui


@unittest.skipUnless(os.environ.get('TRAINSTUFF_GUI_TESTS') == '1', 'Requires a desktop Tk session')
class TrainLibraryUITests(unittest.TestCase):
    setUp = test_external_ui.ExternalUITests.setUp

    def open_library(self):
        self.app.backend.reconfigure = Mock()
        self.app.setup(library_only=True)
        dialog = self.app.setup_dialog
        self.assertEqual(dialog.title(), 'Train library')
        self.assertEqual(set(dialog.pages), {'trains', 'saved_trains'})
        return dialog

    def apply_and_refresh(self, dialog):
        dialog.save_button.invoke()
        self.assertFalse(dialog.winfo_exists())
        self.root.update()
        self.app.backend.reconfigure.assert_not_called()

    def test_library_fields_and_actions_fit_both_tabs(self):
        dialog = self.open_library()
        # Saving applies staged membership changes to the main train controls.
        save = dialog.save_button
        self.assertEqual(save.cget('text'), 'Save and apply library')
        for listing, _ in dialog.pages.values():
            page = listing.master.master
            dialog.notebook.select(page)
            self.root.update()
            self.assertTrue(save.winfo_viewable())
            self.assertGreaterEqual(save.winfo_height(), save.winfo_reqheight())
            self.assertLessEqual(save.winfo_rooty()+save.winfo_reqheight(),
                                 dialog.winfo_rooty()+dialog.winfo_height())
            pending = [page]
            while pending:
                widget = pending.pop()
                pending.extend(widget.winfo_children())
                if widget.winfo_class() not in ('TEntry', 'TButton', 'TCombobox'):
                    continue
                self.assertTrue(widget.winfo_viewable())
                self.assertLessEqual(widget.winfo_rootx()+widget.winfo_reqwidth(),
                                     dialog.winfo_rootx()+dialog.winfo_width())
                self.assertLessEqual(widget.winfo_rooty()+widget.winfo_reqheight(),
                                     dialog.winfo_rooty()+dialog.winfo_height())

    def test_save_button_remains_visible_in_each_setup_window(self):
        for options, label in (({}, 'Save and apply setup'),
                               ({'library_only': True}, 'Save and apply library'),
                               ({'train_id': 'a'}, 'Save train settings')):
            self.app.setup(**options)
            dialog = self.app.setup_dialog
            self.assertEqual(dialog.save_button.cget('text'), label)
            for height in (720, 600):
                with self.subTest(label=label, height=height):
                    dialog.geometry(f'900x{height}')
                    self.root.update()
                    button = dialog.save_button
                    self.assertTrue(button.winfo_viewable())
                    self.assertGreaterEqual(button.winfo_height(), button.winfo_reqheight())
                    self.assertLessEqual(button.winfo_rooty()+button.winfo_reqheight(),
                                         dialog.winfo_rooty()+dialog.winfo_height())
            dialog.destroy()

    def test_add_to_active_from_fleet_setup_updates_main_controls_on_save(self):
        move_train(self.app.config, 'p', active=False)
        self.app.build()
        self.app.backend.reconfigure = Mock()
        self.app.setup()
        dialog = self.app.setup_dialog
        dialog.remove('saved_trains')
        self.assertNotIn('p', self.app.control_cards)
        self.apply_and_refresh(dialog)
        self.assertIn('p', self.app.control_cards)
        self.assertIn('p', [train['id'] for train in load_config(self.app.path)['trains']])

    def test_external_round_trip_preserves_rfid_and_restores_tracking_only(self):
        self.app.config.setdefault('automation', {})['pace_train_id'] = 'p'
        original = copy.deepcopy(self.app.config['trains'][2])
        dialog = self.open_library()
        dialog.refresh('trains', 2)
        with patch('fleet_setup.messagebox.askyesno', return_value=True):
            dialog.remove('trains')
        self.assertEqual(dialog.config_data['saved_trains'][0], original)
        self.apply_and_refresh(dialog)
        self.assertEqual(set(self.app.control_cards), {'a', 'b'})
        self.assertNotIn('01020304', self.app.tracker.tags)
        self.assertEqual(load_config(self.app.path)['saved_trains'][0], original)
        self.assertNotIn('pace_train_id', self.app.config['automation'])
        dialog = self.open_library()
        dialog.remove('saved_trains')
        self.apply_and_refresh(dialog)
        self.assertIn('p', self.app.control_cards)
        self.assertEqual(self.app.tracker.tags['01020304'], 'p')
        self.assertEqual(self.app.supervisor.external, {'p'})
        self.assertIn('Pace train (p)', self.app.pace_choices)
        self.assertIsNone(self.app.supervisor.pace_train)
        self.assertFalse(self.app.supervisor.enabled)
        self.assertTrue(all(call.args[0] != 'p' for call in self.app.backend.speed.call_args_list))

    def test_live_add_and_remove_keep_other_trains_map_and_tracking(self):
        self.app.command('a', 20)
        self.app.command('b', 20)
        self.app.supervisor.enabled = self.app.supervisor.automatic['b'] = True
        self.app.auto_enabled.set(True)
        self.app.zoom, self.app.offset = 1.5, [23., 45.]
        self.app.tracker.clearance.held['b'] = {'2', '1'}
        self.app.tracker.learning.record('b', '2', 1, 20, 500)
        tracker, supervisor, canvas, sensors = self.app.tracker, self.app.supervisor, self.app.canvas, self.app.sensors
        seen = dict(tracker.last_seen)
        held = copy.deepcopy(tracker.clearance.held)
        history = copy.deepcopy(tracker.learning.samples)
        dialog = self.open_library()
        dialog.add('trains')
        self.app.backend.speed.reset_mock()
        self.apply_and_refresh(dialog)
        self.app.backend.speed.assert_not_called()
        self.assertEqual(supervisor.throttle, dict(a=20, b=20, p=0, train_1=0))
        self.assertFalse(supervisor.on_track['train_1'])
        self.assertFalse(supervisor.automatic['train_1'])
        self.assertTrue(supervisor.automatic['b'])
        self.assertEqual(self.app.sliders['b'].get(), 20)
        self.assertEqual(tracker.last_seen, seen)
        self.assertEqual(tracker.clearance.held, held)
        self.assertEqual(tracker.learning.samples, history)
        self.assertEqual(self.app.roster_label.cget('text'), 'ACTIVE TRAINS  /  4')
        dialog = self.open_library()
        dialog.refresh('trains', 0)
        with patch('fleet_setup.messagebox.askyesno', return_value=True):
            dialog.remove('trains')
        self.app.backend.speed.reset_mock()
        self.apply_and_refresh(dialog)
        self.assertEqual([call.args for call in self.app.backend.speed.call_args_list], [('a', 0)])
        self.assertEqual(supervisor.throttle['b'], 20)
        self.assertTrue(supervisor.enabled)
        self.assertTrue(supervisor.automatic['b'])
        self.assertTrue(self.app.demo_running)
        self.assertEqual(tracker.last_seen['b'], seen['b'])
        self.assertEqual(tracker.clearance.held['b'], held['b'])
        self.assertNotIn('a', self.app.control_cards)
        self.assertEqual(self.app.roster_label.cget('text'), 'ACTIVE TRAINS  /  3')
        self.assertIs(self.app.tracker, tracker)
        self.assertIs(self.app.supervisor, supervisor)
        self.assertIs(self.app.canvas, canvas)
        self.assertIs(self.app.sensors, sensors)
        self.assertEqual((self.app.zoom, self.app.offset), (1.5, [23., 45.]))

    def test_existing_ids_are_stable_while_display_names_and_new_ids_are_editable(self):
        dialog = self.open_library()
        self.assertEqual(str(dialog.id_entries['trains'].cget('state')), 'readonly')
        dialog.pages['trains'][1]['name'].set('Renamed train')
        dialog.add('trains')
        self.assertEqual(str(dialog.id_entries['trains'].cget('state')), 'normal')
        dialog.pages['trains'][1]['id'].set('new_id')
        self.apply_and_refresh(dialog)
        self.assertIn('new_id', self.app.control_cards)
        self.assertEqual(self.app.supervisor.trains['a']['name'], 'Renamed train')

    def test_adding_train_during_speed_matching_does_not_start_it_after_detection(self):
        self.app.supervisor.set_pace_train('p')
        self.app.match_external.set(True)
        dialog = self.open_library()
        dialog.add('trains')
        dialog.pages['trains'][1]['tags'].set('31323334')
        self.apply_and_refresh(dialog)
        supervisor = self.app.supervisor
        self.assertEqual(supervisor.pace_train, 'p')
        self.assertIn('train_1', supervisor.operator_paused)
        self.app.tracker.last_seen['train_1'] = ('5', self.app.tracker.clock())
        supervisor.observe('train_1')
        supervisor.tick()
        self.assertEqual(supervisor.throttle['train_1'], 0)
        self.assertTrue(self.app.match_external.get())

    def test_live_ownership_change_keeps_occupancy_and_other_train_running(self):
        self.app.command('a', 20)
        self.app.command('b', 20)
        occupied = self.app.supervisor.occupied()
        self.app.backend.reconfigure = Mock()
        edited = copy.deepcopy(self.app.config)
        edited['trains'][0]['control_mode'] = 'external'
        self.app.backend.speed.reset_mock()
        self.app.apply_setup(edited)
        self.root.update()
        self.assertEqual([call.args for call in self.app.backend.speed.call_args_list], [('a', 0)])
        self.assertEqual(self.app.supervisor.throttle['b'], 20)
        self.assertEqual(self.app.supervisor.occupied(), occupied)
        self.assertIn('a', self.app.supervisor.external)
        self.assertIn('a', self.app.tracker.external)
        self.assertTrue(self.app.card_buttons['a'][0][0].instate(['disabled']))
        edited['trains'][0]['control_mode'] = 'fleet'
        self.app.apply_setup(edited)
        self.assertNotIn('a', self.app.supervisor.external)
        self.assertEqual(self.app.supervisor.throttle['a'], 0)
        self.assertEqual(self.app.supervisor.throttle['b'], 20)
        self.assertEqual(self.app.supervisor.occupied(), occupied)
        self.app.backend.reconfigure.assert_not_called()

    def test_removing_live_speed_source_pauses_followers_without_resetting_tracking(self):
        self.app.command('a', 20)
        self.app.command('b', 20)
        supervisor, tracker = self.app.supervisor, self.app.tracker
        supervisor.set_pace_train('p')
        self.app.match_external.set(True)
        seen = dict(tracker.last_seen)
        dialog = self.open_library()
        dialog.refresh('trains', 2)
        with patch('fleet_setup.messagebox.askyesno', return_value=True):
            dialog.remove('trains')
        self.apply_and_refresh(dialog)
        self.assertIs(self.app.tracker, tracker)
        self.assertIs(self.app.supervisor, supervisor)
        self.assertIsNone(supervisor.pace_train)
        self.assertFalse(self.app.match_external.get())
        self.assertEqual(supervisor.operator_paused, {'a', 'b'})
        self.assertEqual(supervisor.throttle, {'a': 0, 'b': 0})
        self.assertEqual(tracker.last_seen, {tid: seen[tid] for tid in ('a', 'b')})
        self.assertEqual(supervisor.tick(), {})

    def test_cancel_removal_keeps_active_record_and_closing_does_not_save(self):
        before = copy.deepcopy(self.app.config)
        dialog = self.open_library()
        with patch('fleet_setup.messagebox.askyesno', return_value=False):
            dialog.remove('trains')
        self.assertEqual(dialog.config_data['trains'], before['trains'])
        self.assertEqual(dialog.config_data['saved_trains'], [])
        with patch('fleet_setup.messagebox.askyesno', return_value=True):
            dialog.remove('trains')
        dialog.destroy()
        self.assertEqual(self.app.config, before)
        self.app.backend.reconfigure.assert_not_called()

    def test_archiving_while_learning_keeps_latest_history(self):
        self.app.tracker.learning.persist = True
        dialog = self.open_library()
        with patch('fleet_setup.messagebox.askyesno', return_value=True):
            dialog.remove('trains')
        self.app.tracker.learning.record('a', '0', 1, 20, 500)
        history = copy.deepcopy(self.app.config['trains'][0]['section_history'])
        self.apply_and_refresh(dialog)
        self.assertEqual(load_config(self.app.path)['saved_trains'][0]['section_history'], history)

    def test_edit_and_scan_saved_train_without_stopping_active_fleet(self):
        move_train(self.app.config, 'p', active=False)
        self.app.build()
        self.app.tracker.last_seen['a'] = ('0', self.app.tracker.clock())
        self.app.command('a', 20)
        tracker, supervisor = self.app.tracker, self.app.supervisor
        self.app.backend.speed.reset_mock()
        dialog = self.open_library()
        fields = dialog.pages['saved_trains'][1]
        fields['name'].set('Saved external train')
        dialog.arm_scan('saved_trains')
        dialog.receive_tag('21:22:23:24', '0')
        dialog.apply()
        self.assertFalse(dialog.winfo_exists())
        self.assertIs(self.app.tracker, tracker)
        self.assertIs(self.app.supervisor, supervisor)
        self.assertEqual(supervisor.throttle['a'], 20)
        self.app.backend.speed.assert_not_called()
        self.app.backend.reconfigure.assert_not_called()
        stored = self.app.config['saved_trains'][0]
        self.assertEqual(stored['name'], 'Saved external train')
        self.assertEqual(stored['tags'], ['01020304', '21222324'])
        self.assertNotIn('21222324', tracker.tags)
        self.assertEqual(load_config(self.app.path)['saved_trains'][0], stored)

    def test_empty_active_fleet_and_new_saved_external_train(self):
        self.app.config['saved_trains'] = self.app.config['trains']
        self.app.config['trains'] = []
        self.app.build()
        dialog = self.open_library()
        dialog.add('saved_trains')
        fields = dialog.pages['saved_trains'][1]
        fields['name'].set('Another external')
        fields['control_mode'].set('External controller')
        fields['hub_name'].set('')
        fields['tags'].set('31323334')
        dialog.remove('saved_trains')
        self.apply_and_refresh(dialog)
        self.assertEqual(set(self.app.control_cards), {'train_1'})
        self.assertEqual(self.app.supervisor.external, {'train_1'})
        self.assertEqual(self.app.tracker.tags['31323334'], 'train_1')
        self.assertEqual(len(self.app.config['saved_trains']), 3)

    def test_saved_tag_ownership_shown_and_assignable_in_sensors(self):
        move_train(self.app.config, 'p', active=False)
        self.app.build()
        self.app.backend.reconfigure = Mock()
        self.app.manage_sensors()
        dialog = self.app.sensor_dialog
        self.addCleanup(dialog.close)
        self.assertIn('saved', dialog.tags.item('01020304', 'values')[1])
        choice = next(name for name, tid in dialog.train_ids.items() if tid == 'p')
        dialog.train.set(choice)
        dialog.tags.insert('', 'end', iid='31323334', values=('31323334', 'Unassigned', '0', 'Now'))
        dialog.tags.selection_set('31323334')
        dialog.assign_tag()
        self.assertIn('31323334', self.app.config['saved_trains'][0]['tags'])
        self.assertNotIn('31323334', self.app.tracker.tags)
        self.app.backend.reconfigure.assert_not_called()

    def test_delete_saved_train_saves_without_stopping_active_fleet(self):
        move_train(self.app.config, 'p', active=False)
        self.app.build()
        self.app.command('a', 20)
        tracker, supervisor = self.app.tracker, self.app.supervisor
        self.app.backend.speed.reset_mock()
        dialog = self.open_library()
        dialog.arm_scan('saved_trains')
        with patch('fleet_setup.messagebox.askyesno', return_value=True):
            dialog.delete_saved_train()
        self.assertIsNone(dialog.scan_target)
        self.assertEqual(dialog.config_data['saved_trains'], [])
        self.assertTrue(all(var.get() == '' for var in dialog.pages['saved_trains'][1].values()))
        # The change is staged until the existing Save and apply action.
        self.assertEqual(self.app.config['saved_trains'][0]['id'], 'p')
        dialog.receive_tag('31323334', '0')
        dialog.apply()
        self.assertFalse(dialog.winfo_exists())
        self.assertEqual(self.app.config['saved_trains'], [])
        self.assertEqual(load_config(self.app.path)['saved_trains'], [])
        self.assertIs(self.app.tracker, tracker)
        self.assertIs(self.app.supervisor, supervisor)
        self.assertEqual(supervisor.throttle['a'], 20)
        self.app.backend.speed.assert_not_called()
        self.app.backend.reconfigure.assert_not_called()

    def test_cancel_delete_or_close_library_preserves_saved_train(self):
        move_train(self.app.config, 'p', active=False)
        self.app.build()
        original = copy.deepcopy(self.app.config)
        dialog = self.open_library()
        with patch('fleet_setup.messagebox.askyesno', return_value=False):
            dialog.delete_saved_train()
        self.assertEqual(dialog.config_data['saved_trains'], original['saved_trains'])
        with patch('fleet_setup.messagebox.askyesno', return_value=True):
            dialog.delete_saved_train()
        dialog.destroy()
        self.assertEqual(self.app.config, original)
        self.app.backend.reconfigure.assert_not_called()
