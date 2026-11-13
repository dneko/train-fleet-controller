"""Local train photo imports and portable configuration references."""
import copy
from pathlib import Path
import uuid

from PIL import Image, ImageOps

from fleet_setup import save_config


def load_train_photo(source, config_path, size):
    path = Path(source)
    if not path.is_absolute():
        path = Path(config_path).parent / path
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail(size, Image.Resampling.LANCZOS)
            return image.convert('RGBA')
    except Image.DecompressionBombError as exc:
        raise ValueError('This picture is too large. Choose a smaller image.') from exc


def save_train_photo(config_path, config, tid, source=None):
    """Save a photo change before updating the live train; retain shared old assets."""
    train = next(t for t in config['trains'] if t['id'] == tid)
    updated = copy.deepcopy(config)
    target = next(t for t in updated['trains'] if t['id'] == tid)
    imported = None
    try:
        if source:
            image = load_train_photo(Path(source).resolve(), config_path, (1200, 900))
            assets = Path(config_path).parent / 'train_photos'
            assets.mkdir(exist_ok=True)
            imported = assets / (uuid.uuid4().hex + '.png')
            image.save(imported, format='PNG')
            target['photo'] = str(imported.relative_to(Path(config_path).parent))
        else:
            target.pop('photo', None)
        save_config(config_path, updated)
    except Exception:
        if imported is not None:
            imported.unlink(missing_ok=True)
        raise
    if 'photo' in target:
        train['photo'] = target['photo']
    else:
        train.pop('photo', None)
