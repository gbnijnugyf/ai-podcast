"""Lossless transport of one validated MP4, using standard ZIP byte volumes."""
import shutil
import tempfile
from pathlib import Path
import zipfile
from github_output import digest

PART_BYTES = 8 * 1024 * 1024


def pack_video(source, directory, part_bytes=PART_BYTES):
    if source.is_symlink() or not source.is_file() or source.suffix.lower() != '.mp4':
        raise RuntimeError('Archive source must be a regular MP4')
    if part_bytes <= 0:
        raise ValueError('Invalid volume size')
    directory.mkdir(parents=True, exist_ok=True)
    parts, paths = [], []
    stem = "video-" + digest(source) + ".zip"
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        archive = Path(temporary) / 'video.zip'
        # Fixed name and timestamp make retries byte-for-byte deterministic.
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as z:
            info = zipfile.ZipInfo('video.mp4', (1980, 1, 1, 0, 0, 0))
            with source.open('rb') as src, z.open(info, 'w', force_zip64=True) as dst:
                shutil.copyfileobj(src, dst)
        with archive.open('rb') as stream:
            index = 1
            while block := stream.read(part_bytes):
                if index > 999:
                    raise RuntimeError('Too many archive volumes')
                path = directory / f'{stem}.{index:03d}'
                path.write_bytes(block)
                paths.append(path)
                parts.append({'name':path.name, 'size':len(block), 'sha256':digest(path)})
                index += 1
    return {'format':'split-zip-v1', 'parts':parts}, paths


def restore_video(metadata, paths, destination, video):
    parts = metadata.get('parts', [])
    if metadata.get('format') != 'split-zip-v1' or not parts or len(parts) > 999:
        raise RuntimeError('Invalid video archive manifest')
    stem = "video-" + video.get("sha256", "") + ".zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        archive = Path(temporary) / 'video.zip'
        with archive.open('wb') as dst:
            for index, part in enumerate(parts, 1):
                name = part['name']
                if name != f'{stem}.{index:03d}' or name not in paths:
                    raise RuntimeError('Missing or unordered video archive volumes')
                path = paths[name]
                if path.stat().st_size != part['size'] or digest(path) != part['sha256']:
                    raise RuntimeError('Video archive volume checksum mismatch')
                with path.open('rb') as src:
                    shutil.copyfileobj(src, dst)
        restored = Path(temporary) / 'video.mp4'
        with zipfile.ZipFile(archive) as z:
            if z.namelist() != ['video.mp4'] or z.getinfo('video.mp4').file_size != video['size']:
                raise RuntimeError('Archive must contain only the expected video')
            with z.open('video.mp4') as src, restored.open('wb') as dst:
                shutil.copyfileobj(src, dst)
        if restored.stat().st_size != video['size'] or digest(restored) != video['sha256']:
            raise RuntimeError('Restored video checksum mismatch')
        restored.replace(destination)
