"""Save portable named layouts, including imported map and train images."""
import copy
from pathlib import Path
import shutil
import uuid

from fleet_setup import save_config
from fleet_model import all_trains


def save_configuration(destination, config, current_path):
    destination = Path(destination).resolve()
    current_path = Path(current_path).resolve()
    result = copy.deepcopy(config)
    copied = []
    try:
        images = [(result, 'map_image', 'map_images')]
        images.extend((train, 'photo', 'train_photos') for train in all_trains(result))
        if destination.parent != current_path.parent:
            for owner, key, folder in images:
                source = owner.get(key)
                if not source:
                    continue
                source = Path(source)
                if not source.is_absolute():
                    source = current_path.parent / source
                assets = destination.parent / folder
                assets.mkdir(exist_ok=True)
                target = assets / (uuid.uuid4().hex + source.suffix.lower())
                copied.append(target)
                shutil.copyfile(source, target)
                owner[key] = str(target.relative_to(destination.parent))
        return save_config(destination, result)
    except Exception:
        for target in copied:
            target.unlink(missing_ok=True)
        raise
