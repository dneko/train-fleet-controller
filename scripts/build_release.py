"""Build a source ZIP from an explicit list, without packaging local user data."""
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
NAME = 'train-fleet-controller-source'


def main():
    paths = []
    for line in (ROOT / 'release-files.txt').read_text(encoding='utf-8').splitlines():
        name = line.strip()
        if not name or name.startswith('#'):
            continue
        relative = Path(name)
        path = ROOT / relative
        if (relative.is_absolute() or '..' in relative.parts
                or any(parent.is_symlink() for parent in [path, *path.parents])
                or not path.is_file()):
            raise ValueError(f'Invalid or missing release file: {name}')
        if relative in paths:
            raise ValueError(f'Duplicate release file: {name}')
        paths.append(relative)
    destination = ROOT / 'dist'
    destination.mkdir(exist_ok=True)
    archive = destination / f'{NAME}.zip'
    with ZipFile(archive, 'w', compression=ZIP_DEFLATED) as output:
        for relative in sorted(paths):
            output.write(ROOT / relative, f'{NAME}/{relative.as_posix()}')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
    print(f'{archive} ({len(paths)} files)')
    print(f'SHA-256: {digest}')


if __name__ == '__main__':
    main()
