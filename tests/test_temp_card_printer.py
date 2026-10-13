from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tests.card_fixtures import generate_image
from media_selection import MediaSelection, SelectionKind, SelectionOrigin
import temp_card_printer as printer

ROOT = Path(__file__).resolve().parents[1]


class TempCardPrinterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='temp-card-test-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()

    def cli(self, *args):
        return subprocess.run(['/opt/homebrew/bin/python3', '-B',
                               str(ROOT / 'scripts/temp_card_printer.py'), *map(str, args)],
                              capture_output=True, text=True)

    def test_end_to_end_and_numbered_publication(self):
        landscape, portrait = self.root / 'front.jpg', self.root / 'back.png'
        generate_image(landscape)
        generate_image(portrait, '648x1024')
        before = landscape.read_bytes(), portrait.read_bytes()
        first = self.cli(landscape, portrait, '--copies', '5', '--no-open', '--json')
        self.assertEqual(first.returncode, 0, first.stderr)
        summary = json.loads(first.stdout)
        self.assertEqual((summary['pages'], summary['cards']), (2, 10))
        output = Path(summary['output'])
        self.assertEqual(output, self.root / 'Temp Cards.pdf')
        original_pdf = output.read_bytes()
        second = self.cli(landscape, portrait, '--no-open', '--json')
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(Path(json.loads(second.stdout)['output']), self.root / 'Temp Cards-2.pdf')
        self.assertEqual(output.read_bytes(), original_pdf)
        self.assertEqual((landscape.read_bytes(), portrait.read_bytes()), before)
        inspected = subprocess.run(['/usr/bin/sips', '-g', 'pixelWidth', str(output)],
                                   capture_output=True, text=True)
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertRegex(inspected.stdout, r'pixelWidth: \d+')
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(self.root.glob('.temp-cards-*')))

    def test_explicit_output_numbering_and_no_open(self):
        source = self.root / 'front.jpg'
        generate_image(source)
        output = self.root / 'chosen.pdf'
        output.write_bytes(b'keep me')
        stdout = io.StringIO()
        with patch('temp_card_printer.media.run') as run, redirect_stdout(stdout):
            result = printer.main([str(source), '--output', str(output), '--no-open', '--json'])
        self.assertEqual(result, 0)
        run.assert_not_called()
        self.assertEqual(output.read_bytes(), b'keep me')
        self.assertEqual(Path(json.loads(stdout.getvalue())['output']), self.root / 'chosen-2.pdf')

    def test_default_open_is_requested_only_after_publication(self):
        source = self.root / 'front.jpg'
        generate_image(source)
        def open_published(args):
            self.assertEqual(args[0], '/usr/bin/open')
            self.assertTrue(Path(args[1]).read_bytes().startswith(b'%PDF-1.4'))
            return ''
        with patch('temp_card_printer.media.run', side_effect=open_published) as run:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(printer.main([str(source)]), 0)
        run.assert_called_once()

    def test_numbering_does_not_replace_dangling_symlink(self):
        destination = self.root / 'cards.pdf'
        destination.symlink_to(self.root / 'missing.pdf')
        published = printer.publish_pdf(b'new output', destination)
        self.assertEqual(published, self.root / 'cards-2.pdf')
        self.assertTrue(destination.is_symlink())
        self.assertEqual(published.read_bytes(), b'new output')

    def test_cancelled_picker_and_image_selection_kind(self):
        selected = MediaSelection((), SelectionOrigin.PICKER)
        stdout = io.StringIO()
        with patch('temp_card_printer.select_media', return_value=selected) as choose, redirect_stdout(stdout):
            self.assertEqual(printer.main(['--finder', '--no-open']), 0)
        choose.assert_called_once_with(use_finder=True, paths=[], kind=SelectionKind.IMAGE)
        self.assertEqual(stdout.getvalue(), 'No images selected.\n')

    def test_errors_are_concise_and_options_validate_before_picker(self):
        for args in (['--copies', '0'], ['--scale', 'nan'], [str(self.root / 'missing.jpg')]):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(printer.main([*args, '--no-open']), 1)
            self.assertIn('Error:', stderr.getvalue())
            self.assertNotIn('Traceback', stderr.getvalue())

    def test_help_needs_no_scientific_environment(self):
        result = self.cli('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--no-open', result.stdout)
