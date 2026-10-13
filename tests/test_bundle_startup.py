"""CLI startup must leave bundled resources unchanged, even without Python -B."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


class BundleStartupTests(unittest.TestCase):
    def test_performance_audio_help_does_not_write_import_bytecode(self):
        source = Path(__file__).resolve().parents[1]/'scripts'
        with tempfile.TemporaryDirectory() as directory:
            resources = Path(directory)
            for name in ('performance_audio.py', 'media_formats.py'):
                shutil.copy2(source/name, resources/name)
            before = sorted(path.name for path in resources.iterdir())
            result = subprocess.run([sys.executable, '-E', str(resources/'performance_audio.py'), '--help'],
                                    capture_output=True, text=True, env=os.environ.copy())
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sorted(path.name for path in resources.iterdir()), before)
