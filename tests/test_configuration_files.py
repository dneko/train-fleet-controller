import tempfile
import unittest
from pathlib import Path
from configuration_files import save_configuration
from fleet_model import load_config


class ConfigurationFilesTests(unittest.TestCase):
    def test_save_as_keeps_saved_train_photos_portable(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base/'new').mkdir()
            (base/'stored.png').write_bytes(b'saved train photo')
            config = {'trains': [], 'readers': [], 'saved_trains': [
                dict(id='p', name='External', control_mode='external', tags=['01020304'], photo='stored.png')]}
            saved = save_configuration(base/'new'/'fleet.json', config, base/'fleet.json')
            train = saved['saved_trains'][0]
            self.assertEqual((base/'new'/train['photo']).read_bytes(), b'saved train photo')
            self.assertEqual(train['tags'], ['01020304'])
            self.assertEqual(load_config(base/'new'/'fleet.json'), saved)
            self.assertEqual(config['saved_trains'][0]['photo'], 'stored.png')

    def test_save_as_copies_image_and_does_not_mutate_source(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old, new = base/'old', base/'new'
            old.mkdir()
            new.mkdir()
            (old/'layout.png').write_bytes(b'image bytes')
            config = {'trains':[], 'readers':[], 'map_image':'layout.png', 'map_m_per_unit':.008}
            saved = save_configuration(new/'layout.json', config, old/'fleet.json')
            self.assertEqual(load_config(new/'layout.json'),saved)
            self.assertEqual((new/saved['map_image']).read_bytes(),b'image bytes')
            self.assertEqual(config['map_image'],'layout.png')
            self.assertFalse((old/'fleet.json').exists())

    def test_same_folder_keeps_image_reference(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            config = {'trains':[], 'readers':[], 'map_image':'map_images/layout.png'}
            saved = save_configuration(base/'other.json', config, base/'fleet.json')
            self.assertEqual(saved['map_image'],config['map_image'])

    def test_failed_save_preserves_destination_and_cleans_image(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base/'new').mkdir()
            (base/'map.png').write_bytes(b'image bytes')
            dest = base/'new'/'bad.json'
            dest.write_text('original')
            with self.assertRaises(ValueError):
                save_configuration(dest, {'trains':[{}], 'readers':[], 'map_image':'map.png'},base/'fleet.json')
            self.assertEqual(dest.read_text(),'original')
            self.assertEqual(list((dest.parent/'map_images').iterdir()),[])

    def test_save_as_copies_train_photos_and_keeps_original_references(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base/'new').mkdir()
            (base/'train.png').write_bytes(b'train photo')
            config = {'trains': [{'id': 'a', 'name': 'A', 'hub_name': 'A', 'photo': 'train.png'}],
                      'readers': []}
            same = save_configuration(base/'same.json', config, base/'fleet.json')
            self.assertEqual(same['trains'][0]['photo'], 'train.png')
            saved = save_configuration(base/'new'/'fleet.json', config, base/'fleet.json')
            photo = saved['trains'][0]['photo']
            self.assertEqual(Path(photo).parent, Path('train_photos'))
            self.assertEqual((base/'new'/photo).read_bytes(), b'train photo')
            self.assertEqual(config['trains'][0]['photo'], 'train.png')

    def test_missing_photo_rolls_back_all_copies_and_preserves_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base/'new').mkdir()
            (base/'map.png').write_bytes(b'map')
            (base/'train.png').write_bytes(b'photo')
            destination = base/'new'/'fleet.json'
            destination.write_text('original')
            config = {'trains': [
                {'id': 'a', 'name': 'A', 'hub_name': 'A', 'photo': 'train.png'},
                {'id': 'b', 'name': 'B', 'hub_name': 'B', 'photo': 'missing.png'}],
                'readers': [], 'map_image': 'map.png'}
            with self.assertRaises(OSError):
                save_configuration(destination, config, base/'fleet.json')
            self.assertEqual(destination.read_text(), 'original')
            self.assertEqual(list((base/'new'/'map_images').iterdir()), [])
            self.assertEqual(list((base/'new'/'train_photos').iterdir()), [])
