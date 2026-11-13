"""Saved trains retain their identity without participating in live operation."""
import copy
import json
from pathlib import Path
import queue
import tempfile
import unittest

from fleet_automation import Supervisor
from fleet_controller import Backend
from fleet_model import all_trains, load_config
from fleet_setup import move_train, register_tag, save_config
from loop_tracking import LoopTracker
from train_settings import can_apply_live, merge_train_edits
from test_external_control import configuration


class TrainLibraryTests(unittest.TestCase):
    def setUp(self):
        self.config = configuration()
        self.config['trains'][0].update(tags=['11121314'], rear_tag='21222324',
                                       photo='train_photos/a.png', length_m=1.2)
        LoopTracker(self.config).learning.record('a', '0', 1, 20, 500)

    def test_archive_reload_and_reactivate_preserve_both_kinds_of_train(self):
        originals = copy.deepcopy(self.config['trains'])
        for train in originals:
            move_train(self.config, train['id'], active=False)
        self.assertEqual(self.config['trains'], [])
        self.assertEqual(self.config['saved_trains'], originals)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'fleet.json'
            save_config(path, self.config)
            restored = load_config(path)
        for train in originals:
            move_train(restored, train['id'], active=True)
        self.assertEqual(restored['trains'], originals)
        self.assertEqual(restored['saved_trains'], [])
        self.assertEqual(LoopTracker(restored).learning.get('a', '0')['samples'], 1)

    def test_saved_trains_have_no_connections_tracking_or_occupancy(self):
        move_train(self.config, 'a', active=False)
        move_train(self.config, 'p', active=False)
        tracker = LoopTracker(self.config)
        supervisor = Supervisor(self.config, tracker)
        backend = Backend.__new__(Backend)
        backend.events = queue.Queue()
        self.assertEqual(set(backend.connections(self.config)), {'b'})
        for uid in ('11121314', '21222324', '01020304'):
            self.assertIsNone(tracker.ingest(json.dumps(dict(reader='0', uid=uid)), '0')[0])
        self.assertFalse(tracker.last_seen)
        self.assertEqual(set(supervisor.trains), {'b'})
        self.assertFalse(supervisor.external)
        move_train(self.config, 'p', active=True)
        tracker = LoopTracker(self.config)
        self.assertEqual(tracker.ingest(json.dumps(dict(reader='0', uid='01020304')), '0')[0], 'p')
        self.assertEqual(Supervisor(self.config, tracker).external, {'p'})
        self.assertEqual(set(backend.connections(self.config)), {'b'})

    def test_archiving_pace_source_clears_selection_without_changing_train(self):
        self.config['automation'] = dict(pace_train_id='p')
        original = copy.deepcopy(self.config['trains'][2])
        move_train(self.config, 'p', active=False)
        self.assertNotIn('pace_train_id', self.config['automation'])
        self.assertEqual(self.config['saved_trains'][0], original)

    def test_duplicates_across_active_and_saved_are_rejected_without_overwriting(self):
        for field, value in [('id', 'a'), ('hub_name', 'A'), ('tags', ['11:12:13:14']),
                             ('rear_tag', '21:22:23:24')]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as folder:
                path = Path(folder)/'fleet.json'
                save_config(path, self.config)
                before = path.read_bytes()
                edited = copy.deepcopy(self.config)
                edited['saved_trains'] = [dict(id='saved', name='Saved', hub_name='Saved',
                                               tags=['31323334'])]
                edited['saved_trains'][0][field] = value
                with self.assertRaises(ValueError):
                    save_config(path, edited)
                self.assertEqual(path.read_bytes(), before)

    def test_malformed_saved_entries_are_validated(self):
        for saved in ('invalid', [{}], [dict(id='x', name='X', control_mode='unknown')]):
            with self.subTest(saved=saved), tempfile.TemporaryDirectory() as folder:
                config = dict(self.config, saved_trains=saved)
                with self.assertRaises(ValueError):
                    save_config(Path(folder)/'fleet.json', config)

    def test_register_saved_tags_and_reject_stealing_in_both_directions(self):
        move_train(self.config, 'p', active=False)
        register_tag(self.config, 'p', '31:32:33:34', role='rear')
        self.assertEqual(self.config['saved_trains'][0]['rear_tag'], '31323334')
        for target, uid in [('p', '11121314'), ('a', '01020304'), ('a', '31323334')]:
            with self.assertRaisesRegex(ValueError, 'already belongs'):
                register_tag(self.config, target, uid)

    def test_archiving_rebases_new_learning_and_unrelated_live_edits(self):
        original = copy.deepcopy(self.config)
        edited = copy.deepcopy(original)
        edited['trains'][0]['name'] = 'Renamed train'
        move_train(edited, 'a', active=False)
        LoopTracker(self.config).learning.record('a', '0', 1, 24, 600)
        self.config['trains'][0]['photo'] = 'new.png'
        self.config['view'] = dict(activity_log=True)
        merged = merge_train_edits(self.config, original, edited)
        saved = merged['saved_trains'][0]
        self.assertEqual(saved['name'], 'Renamed train')
        self.assertEqual(saved['section_history'], self.config['trains'][0]['section_history'])
        self.assertEqual(saved['photo'], 'new.png')
        self.assertEqual(merged['view'], self.config['view'])
        self.assertEqual(len(all_trains(merged)), 3)
        self.assertFalse(can_apply_live(self.config, merged))

    def test_saved_edits_are_live_but_reactivation_requires_rebuild(self):
        move_train(self.config, 'p', active=False)
        edited = copy.deepcopy(self.config)
        edited['saved_trains'][0]['tags'].append('31323334')
        self.assertTrue(can_apply_live(self.config, edited))
        move_train(edited, 'p', active=True)
        self.assertFalse(can_apply_live(self.config, edited))
