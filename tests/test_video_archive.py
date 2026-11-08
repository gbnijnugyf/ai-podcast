import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from video_archive import pack_video, restore_video

class ArchiveTests(unittest.TestCase):
    def test_roundtrip_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'master.mp4'
            original = bytes(range(256)) * 100
            source.write_bytes(original)
            metadata, paths = pack_video(source, root / 'parts', 4096)
            self.assertGreater(len(paths), 1)
            self.assertTrue(all(p.stat().st_size <= 4096 for p in paths))
            output = root / 'restored.mp4'
            restore_video(metadata, {p.name: p for p in paths}, output,
                          {'size': len(original), 'sha256': hashlib.sha256(original).hexdigest()})
            self.assertEqual(output.read_bytes(), original)
            paths[0].write_bytes(b'x' * paths[0].stat().st_size)
            output.unlink()
            with self.assertRaisesRegex(RuntimeError, 'checksum'):
                restore_video(metadata, {p.name:p for p in paths}, output,
                              {'size': len(original), 'sha256': hashlib.sha256(original).hexdigest()})
            self.assertFalse(output.exists())

    def test_missing_and_reordered_parts_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'master.mp4'
            source.write_bytes(b'x'*10000)
            metadata, paths = pack_video(source, root / 'parts', 4096)
            with self.assertRaises(RuntimeError):
                restore_video(metadata, {}, root/'out.mp4', {})
            metadata['parts'].reverse()
            with self.assertRaises(RuntimeError):
                restore_video(metadata, {p.name:p for p in paths}, root/'out.mp4', {})
