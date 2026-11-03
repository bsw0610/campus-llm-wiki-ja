import tempfile
import unittest
from pathlib import Path
from scripts.init_layout import DIRECTORIES, initialize


class LayoutTests(unittest.TestCase):
    def test_repeat_keeps_existing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'raw').mkdir()
            note = root/'raw/source.md'
            original = '日本語\r\n한국어\n'.encode()
            note.write_bytes(original)
            initialize(root)
            self.assertEqual(initialize(root), [])
            self.assertEqual(note.read_bytes(), original)
            self.assertTrue(all((root/p).is_dir() for p in DIRECTORIES))

    def test_file_collision_has_no_partial_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'wiki').write_text('keep')
            with self.assertRaises(ValueError):
                initialize(root)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['wiki'])
            self.assertEqual((root/'wiki').read_text(), 'keep')

    def test_external_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as external:
            root = Path(directory)
            (root/'raw').symlink_to(external, target_is_directory=True)
            with self.assertRaises(ValueError):
                initialize(root)
            self.assertEqual(list(Path(external).iterdir()), [])
            self.assertFalse((root/'00 Inbox').exists())


if __name__ == '__main__':
    unittest.main()
