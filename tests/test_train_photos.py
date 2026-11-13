import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from fleet_model import load_config
from fleet_setup import save_config
from train_photos import load_train_photo, save_train_photo


class TrainPhotoTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.base = Path(self.folder.name)
        self.path = self.base / 'fleet.json'
        self.config = {'trains': [{'id': 'a', 'name': 'A', 'hub_name': 'A', 'tags': []}],
                       'readers': []}
        save_config(self.path, self.config)

    def test_import_corrects_orientation_and_survives_original_removal(self):
        source = self.base / 'original.jpg'
        exif = Image.Exif()
        exif[274] = 6
        Image.new('RGB', (1800, 600), 'red').save(source, exif=exif)
        train = self.config['trains'][0]
        save_train_photo(self.path, self.config, 'a', source)
        source.unlink()
        loaded = load_config(self.path)
        self.assertEqual(loaded, self.config)
        self.assertIs(train, self.config['trains'][0])
        self.assertFalse(Path(train['photo']).is_absolute())
        photo = load_train_photo(train['photo'], self.path, (1200, 900))
        self.assertEqual(photo.size, (300, 900))
        self.assertEqual(load_train_photo(train['photo'], self.path, (76, 48)).size, (16, 48))

    def test_replace_and_remove_preserve_previous_asset_for_other_layouts(self):
        source = self.base / 'original.png'
        Image.new('RGBA', (200, 50), (50, 60, 70, 128)).save(source)
        save_train_photo(self.path, self.config, 'a', source)
        old = self.base / self.config['trains'][0]['photo']
        save_train_photo(self.path, self.config, 'a', source)
        new = self.base / self.config['trains'][0]['photo']
        self.assertNotEqual(old, new)
        self.assertEqual(load_train_photo(str(new), self.path, (200, 50)).getpixel((0, 0))[3], 128)
        save_train_photo(self.path, self.config, 'a')
        self.assertNotIn('photo', load_config(self.path)['trains'][0])
        self.assertNotIn('photo', self.config['trains'][0])
        self.assertTrue(old.exists())
        self.assertTrue(new.exists())

    def test_invalid_image_and_failed_save_leave_existing_photo_unchanged(self):
        source = self.base / 'original.png'
        Image.new('RGB', (100, 50), 'blue').save(source)
        save_train_photo(self.path, self.config, 'a', source)
        previous = copy.deepcopy(self.config)
        saved_bytes = self.path.read_bytes()
        assets = list((self.base / 'train_photos').iterdir())
        invalid = self.base / 'broken.png'
        invalid.write_bytes(b'not an image')
        with self.assertRaises(OSError):
            save_train_photo(self.path, self.config, 'a', invalid)
        with patch('train_photos.save_config', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                save_train_photo(self.path, self.config, 'a', source)
            with self.assertRaises(OSError):
                save_train_photo(self.path, self.config, 'a')
        self.assertEqual(self.config, previous)
        self.assertEqual(self.path.read_bytes(), saved_bytes)
        self.assertEqual(list((self.base / 'train_photos').iterdir()), assets)

    def test_photo_path_validation_allows_missing_files(self):
        self.config['trains'][0]['photo'] = 'train_photos/missing.png'
        save_config(self.path, self.config)
        self.assertEqual(load_config(self.path), self.config)
        self.config['trains'][0]['photo'] = 123
        with self.assertRaises(ValueError):
            save_config(self.path, self.config)
