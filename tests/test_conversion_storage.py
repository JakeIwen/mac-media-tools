"""Run with PYTHONPATH=scripts; test public conversion storage helpers."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import conversion_storage as storage
from conversion_settings import ConversionSettings, VideoSettings
import performance_audio_effects as effects


class ConversionStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def value(self):
        audio = effects.load_presets()["presets"]["jake-preferred"]
        return ConversionSettings(audio, VideoSettings()).wire()

    def test_interrupted_serialization_leaves_no_final_or_temporary_file(self):
        path = self.root / "job.json"

        def interrupted(value, output, **kwargs):
            output.write('{"partial":')
            raise OSError("interrupted")

        with mock.patch.object(storage.json, "dump", side_effect=interrupted):
            with self.assertRaisesRegex(OSError, "interrupted"):
                storage.write_json(path, {"complete": True})
        self.assertFalse(path.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_exclusive_write_preserves_preexisting_file(self):
        path = self.root / "preset-existing.json"
        original = b'{"owner":"existing"}\n'
        path.write_bytes(original)
        with self.assertRaises(FileExistsError):
            storage.write_json(path, {"owner": "replacement"}, exclusive=True)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual([item.name for item in self.root.iterdir()], [path.name])

    def directory_fd(self, path=None):
        descriptor = os.open(path or self.root, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, descriptor)
        return descriptor

    def test_descriptor_exclusive_write_preserves_existing_file(self):
        descriptor = self.directory_fd()
        path = self.root / 'session.json'
        storage.write_json(path.name, {'owner': 'existing'}, exclusive=True, dir_fd=descriptor)
        original = path.read_bytes()
        with self.assertRaisesRegex(storage.media.Error, 'Refusing to overwrite'):
            storage.write_json(path.name, {'owner': 'replacement'}, exclusive=True, dir_fd=descriptor)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual([item.name for item in self.root.iterdir()], [path.name])
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_descriptor_replacement_matches_path_based_serialization(self):
        descriptor = self.directory_fd()
        path = self.root / 'session.json'
        control = self.root / 'control.json'
        path.write_bytes(b'old metadata')
        value = {'owner': 'replacement', 'notes': 'sing\nagain'}
        storage.write_json(control, value)
        storage.write_json(path.name, value, dir_fd=descriptor)
        self.assertEqual(path.read_bytes(), control.read_bytes())
        self.assertEqual(set(self.root.iterdir()), {path, control})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_descriptor_failures_remove_temporary_and_preserve_destination(self):
        descriptor = self.directory_fd()
        path = self.root / 'session.json'
        path.write_bytes(b'original')
        for module, name in ((storage.json, 'dump'), (storage.os, 'fsync'),
                             (storage.os, 'link'), (storage.os, 'replace')):
            with self.subTest(stage=name), mock.patch.object(module, name, side_effect=OSError('interrupted')):
                with self.assertRaisesRegex(OSError, 'interrupted'):
                    storage.write_json(path.name, {'complete': True}, exclusive=name == 'link',
                                       dir_fd=descriptor)
            self.assertEqual(path.read_bytes(), b'original')
            self.assertEqual(list(self.root.iterdir()), [path])

    def test_descriptor_rejects_non_basename_destinations(self):
        descriptor = self.directory_fd()
        for name in ('', '.', '..', '../outside.json', 'child/file.json', 'a\\b',
                     'a\x00b', str(self.root / 'absolute.json')):
            with self.subTest(name=name), self.assertRaises(storage.media.Error):
                storage.write_json(name, {}, dir_fd=descriptor)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_descriptor_publication_stays_anchored_after_directory_replacement(self):
        original = self.root / 'original'
        moved = self.root / 'moved'
        original.mkdir()
        descriptor = self.directory_fd(original)
        original.rename(moved)
        original.mkdir()
        storage.write_json('session.json', {'generation': 1}, exclusive=True, dir_fd=descriptor)
        storage.write_json('session.json', {'generation': 2}, dir_fd=descriptor)
        self.assertEqual(json.loads((moved / 'session.json').read_text()), {'generation': 2})
        self.assertEqual(list(original.iterdir()), [])
        self.assertEqual([path.name for path in moved.iterdir()], ['session.json'])

    def test_invalid_preset_does_not_poison_valid_library(self):
        valid = self.root / "preset-valid.json"
        invalid = self.root / "preset-invalid.json"
        storage.write_json(valid, self.value(), exclusive=True)
        invalid.write_text("{broken", encoding="utf-8")

        presets, warnings = storage.read_presets(self.root, ConversionSettings.parse)

        self.assertEqual(presets, {"preset-valid": self.value()})
        self.assertEqual(len(warnings), 1)
        self.assertIn(invalid.name, warnings[0])
        self.assertNotIn("preset-valid", warnings[0])
        self.assertEqual(json.loads(valid.read_text(encoding="utf-8")), self.value())


if __name__ == "__main__":
    unittest.main()
