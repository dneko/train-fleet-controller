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
        self.app.backend.reconfigure = Mock(side_effect=lambda config: self.app.events.put(('configured', config)))
        self.app.setup(library_only=True)
        dialog = self.app.setup_dialog
        self.assertEqual(dialog.title(), 'Train library')
        self.assertEqual(set(dialog.pages), {'trains', 'saved_trains'})
        return dialog

    def apply_and_rebuild(self, dialog):
        dialog.apply()
        self.assertFalse(dialog.winfo_exists())
        self.app.poll()
        self.root.update()

    def test_library_fields_and_actions_fit_both_tabs(self):
        dialog = self.open_library()
        for listing, _ in dialog.pages.values():
            page = listing.master.master
            dialog.notebook.select(page)
            self.root.update()
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

    def test_external_round_trip_preserves_rfid_and_restores_tracking_only(self):
        self.app.config.setdefault('automation', {})['pace_train_id'] = 'p'
        original = copy.deepcopy(self.app.config['trains'][2])
        dialog = self.open_library()
        dialog.refresh('trains', 2)
        with patch('fleet_setup.messagebox.askyesno', return_value=True):
            dialog.remove('trains')
        self.assertEqual(dialog.config_data['saved_trains'][0], original)
        self.apply_and_rebuild(dialog)
        self.assertEqual(set(self.app.control_cards), {'a', 'b'})
        self.assertNotIn('01020304', self.app.tracker.tags)
        self.assertEqual(load_config(self.app.path)['saved_trains'][0], original)
        self.assertNotIn('pace_train_id', self.app.config['automation'])
        dialog = self.open_library()
        dialog.remove('saved_trains')
        self.apply_and_rebuild(dialog)
        self.assertIn('p', self.app.control_cards)
        self.assertEqual(self.app.tracker.tags['01020304'], 'p')
        self.assertEqual(self.app.supervisor.external, {'p'})
        self.assertIn('Pace train (p)', self.app.pace_choices)
        self.assertIsNone(self.app.supervisor.pace_train)
        self.assertFalse(self.app.supervisor.enabled)
        self.assertTrue(all(call.args[0] != 'p' for call in self.app.backend.speed.call_args_list))

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
        self.apply_and_rebuild(dialog)
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
        self.apply_and_rebuild(dialog)
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
