"""Desktop program viewer and upload controls, with no Bluetooth hardware."""
import copy
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from hub_program import read_program
import test_external_ui


@unittest.skipUnless(os.environ.get('TRAINSTUFF_GUI_TESTS') == '1', 'Requires a desktop Tk session')
class HubProgramUITests(unittest.TestCase):
    setUp = test_external_ui.ExternalUITests.setUp

    def live_viewer(self, tid='a'):
        self.app.demo = False
        self.app.backend.upload_program = Mock()
        self.app.backend.update_hubs = Mock()
        self.app.show_hub_program(tid)
        self.root.update()
        return self.app.program_dialog

    def test_view_copy_export_exact_bundled_source_without_editing(self):
        self.app.show_hub_program()
        dialog = self.app.program_dialog
        self.assertEqual(dialog.code.get('1.0', 'end-1c'), read_program())
        self.assertEqual(str(dialog.code['state']), 'disabled')
        dialog.copy_code()
        self.assertEqual(self.root.clipboard_get(), read_program())
        destination = Path(self.folder.name)/'copy.py'
        with patch('hub_program_ui.filedialog.asksaveasfilename', return_value=str(destination)):
            dialog.save_copy()
        self.assertEqual(destination.read_text(), read_program())
        self.assertTrue(dialog.upload_button.instate(['disabled']))

    def test_external_train_cannot_be_upload_target(self):
        dialog = self.live_viewer('p')
        self.assertEqual(dialog.choice.get(), '')
        self.assertTrue(dialog.upload_button.instate(['disabled']))
        self.assertEqual({t['id'] for t in dialog.trains.values()}, {'a', 'b'})
        with self.assertRaises(ValueError):
            self.app.upload_hub_program('p', '', read_program())
        self.app.backend.upload_program.assert_not_called()

    def test_cancel_confirmation_does_not_stop_or_upload(self):
        self.app.command('a', 20)
        dialog = self.live_viewer()
        self.app.backend.speed.reset_mock()
        with patch('hub_program_ui.messagebox.askyesno', return_value=False):
            dialog.upload()
        self.app.backend.speed.assert_not_called()
        self.app.backend.upload_program.assert_not_called()
        self.assertEqual(self.app.supervisor.throttle['a'], 20)

    def test_upload_holds_target_preserves_positions_and_other_train(self):
        self.app.command('a', 20)
        self.app.command('b', 20)
        dialog = self.live_viewer()
        seen = copy.deepcopy(self.app.tracker.last_seen)
        with patch('hub_program_ui.messagebox.askyesno', return_value=True):
            dialog.upload()
        self.app.backend.upload_program.assert_called_once_with('a', read_program())
        self.assertEqual(self.app.supervisor.throttle['a'], 0)
        self.assertEqual(self.app.supervisor.throttle['b'], 20)
        self.assertEqual(self.app.tracker.last_seen, seen)
        self.assertTrue(dialog.upload_button.instate(['disabled']))
        self.assertEqual(str(self.app.sliders['a']['state']), 'disabled')
        self.app.backend.speed.reset_mock()
        self.app.command('a', 60)
        self.app.toggle_force('a')
        self.app.resume('a')
        self.app.send('a', 60)
        self.assertFalse(any(c.args == ('a', 60) for c in self.app.backend.speed.call_args_list))
        self.app.events.put(('ready', 'a', True))
        self.app.demo_running = False
        self.app.poll()
        self.assertFalse(self.app.supervisor.ready['a'])
        self.assertEqual(self.app.supervisor.throttle['a'], 0)
        with self.assertRaisesRegex(ValueError, 'upload'):
            self.app.apply_setup(copy.deepcopy(self.app.config))
        self.app.events.put(('program_upload', 'a', 'complete', 'Uploaded and verified'))
        self.app.events.put(('ready', 'a', True))
        self.app.poll()
        self.assertFalse(self.app.program_uploads)
        self.assertTrue(self.app.supervisor.ready['a'])
        self.assertNotIn('a', self.app.supervisor.operator_paused)
        self.assertEqual(self.app.supervisor.throttle['a'], 0)
        self.assertEqual(dialog.status.get(), 'Uploaded and verified')
        self.app.command('a', 20)
        self.assertEqual(self.app.supervisor.throttle['a'], 20)

    def test_paced_train_stays_paused_when_upload_finishes(self):
        self.app.supervisor.set_pace_train('p')
        dialog = self.live_viewer()
        with patch('hub_program_ui.messagebox.askyesno', return_value=True):
            dialog.upload()
        self.app.events.put(('program_upload', 'a', 'complete', 'Uploaded and verified'))
        self.app.events.put(('ready', 'a', True))
        self.app.demo_running = False
        self.app.poll()
        self.assertIn('a', self.app.supervisor.operator_paused)
        self.assertEqual(self.app.supervisor.throttle['a'], 0)

    def test_failure_visible_after_reopening_and_retry_enabled(self):
        dialog = self.live_viewer()
        with patch('hub_program_ui.messagebox.askyesno', return_value=True):
            dialog.upload()
        dialog.destroy()
        self.app.events.put(('program_upload', 'a', 'failed', 'Upload failed: connection lost'))
        self.app.demo_running = False
        self.app.poll()
        self.app.show_hub_program('a')
        dialog = self.app.program_dialog
        self.assertEqual(dialog.status.get(), 'Upload failed: connection lost')
        self.assertFalse(dialog.upload_button.instate(['disabled']))
        self.assertEqual(self.app.supervisor.throttle['a'], 0)

    def test_stale_hub_selection_is_rejected_without_upload(self):
        dialog = self.live_viewer()
        self.app.supervisor.trains['a']['hub_name'] = 'Renamed hub'
        with patch('hub_program_ui.messagebox.askyesno', return_value=True), \
                patch('hub_program_ui.messagebox.showerror') as error:
            dialog.upload()
        error.assert_called_once()
        self.app.backend.upload_program.assert_not_called()

    def test_update_hubs_stops_only_active_fleet_trains_and_waits_for_all_results(self):
        self.app.command('a', 20)
        self.app.command('b', 30)
        dialog = self.live_viewer()
        seen = copy.deepcopy(self.app.tracker.last_seen)
        dialog.update_button.invoke()
        self.app.backend.update_hubs.assert_called_once_with(read_program())
        self.assertEqual(self.app.program_uploads, {'a', 'b'})
        self.assertEqual(dialog.pending, {'a', 'b'})
        self.assertEqual(self.app.supervisor.throttle['a'], 0)
        self.assertEqual(self.app.supervisor.throttle['b'], 0)
        self.assertEqual(self.app.tracker.last_seen, seen)
        self.assertEqual(dialog.tabs.select(), str(dialog.debug_frame))
        self.assertTrue(dialog.update_button.instate(['disabled']))
        self.app.events.put(('program_upload', 'a', 'complete', 'Uploaded and verified'))
        self.app.demo_running = False
        self.app.poll()
        self.assertTrue(dialog.busy)
        self.assertEqual(self.app.program_uploads, {'b'})
        self.app.events.put(('program_upload', 'b', 'failed', 'Hub b not found'))
        self.app.poll()
        self.assertFalse(dialog.busy)
        self.assertIn('1 succeeded; 1 failed', dialog.status.get())
        self.assertEqual(dialog.progress.set('b', 'status'), 'Hub b not found')
        self.assertFalse(dialog.update_button.instate(['disabled']))

    def test_debug_log_can_be_copied_saved_and_reopened(self):
        dialog = self.live_viewer()
        self.app.events.put(('program_debug', None, 'Scanning for active names'))
        self.app.events.put(('program_debug', 'a', 'RSSI=-42; connected; MTU=23'))
        self.app.demo_running = False
        self.app.poll()
        debug = dialog.debug_log.get('1.0', 'end-1c')
        self.assertIn('Scanning for active names', debug)
        self.assertIn('RSSI=-42', debug)
        dialog.copy_debug()
        self.assertEqual(self.root.clipboard_get(), debug)
        destination = Path(self.folder.name)/'debug.txt'
        with patch('hub_program_ui.filedialog.asksaveasfilename', return_value=str(destination)):
            dialog.save_debug()
        self.assertEqual(destination.read_text(), debug)
        dialog.destroy()
        self.app.show_hub_program()
        self.assertEqual(self.app.program_dialog.debug_log.get('1.0', 'end-1c'), debug)

    def test_batch_rejects_stale_active_list_before_stopping(self):
        self.app.command('a', 20)
        dialog = self.live_viewer()
        self.app.supervisor.trains['a']['hub_name'] = 'New hub name'
        with patch('hub_program_ui.messagebox.showerror') as error:
            dialog.update_button.invoke()
        error.assert_called_once()
        self.app.backend.update_hubs.assert_not_called()
        self.assertEqual(self.app.supervisor.throttle['a'], 20)

    def test_file_update_hubs_starts_scan_flow_without_preconnecting(self):
        self.app.demo = False
        self.app.backend.update_hubs = Mock()
        self.app.update_hubs()
        self.app.backend.update_hubs.assert_called_once_with(read_program())
        self.assertTrue(self.app.program_dialog.busy)

    def test_train_settings_opens_viewer_and_keeps_fleet_controls_accessible(self):
        self.app.setup('b')
        settings = self.app.setup_dialog
        buttons = [w for w in settings.winfo_children() if w.winfo_class() == 'TButton']
        next(w for w in buttons if w['text'] == 'City Hub program…').invoke()
        dialog = self.app.program_dialog
        self.assertEqual(dialog.trains[dialog.choice.get()]['id'], 'b')
        self.assertIsNone(self.root.grab_current())
        self.root.update()
        self.assertLessEqual(dialog.upload_button.winfo_rootx()+dialog.upload_button.winfo_reqwidth(),
                             dialog.winfo_rootx()+dialog.winfo_width())
        dialog.destroy()
        self.assertEqual(self.root.grab_current(), settings)
