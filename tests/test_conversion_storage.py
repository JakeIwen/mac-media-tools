"""Run with PYTHONPATH=macbook/scripts; test public conversion storage helpers."""

from dataclasses import asdict
import json
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

    def test_invalid_preset_does_not_poison_valid_library(self):
        valid = self.root / "preset-valid.json"
        invalid = self.root / "preset-invalid.json"
        storage.write_json(valid, self.value(), exclusive=True)
        invalid.write_text("{broken", encoding="utf-8")

        presets, warnings = storage.read_presets(self.root)

        self.assertEqual(presets, {"preset-valid": self.value()})
        self.assertEqual(len(warnings), 1)
        self.assertIn(invalid.name, warnings[0])
        self.assertNotIn("preset-valid", warnings[0])
        self.assertEqual(json.loads(valid.read_text(encoding="utf-8")), self.value())


if __name__ == "__main__":
    unittest.main()
