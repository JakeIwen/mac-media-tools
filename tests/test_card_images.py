from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from card_images import MAX_IMAGE_BYTES, exif_orientation, jpeg_dimensions, load_card_image
from tests.card_fixtures import exif_segment, generate_image, jpeg_header, segment
from performance_audio import Error


class JpegParserTests(unittest.TestCase):
    def test_frame_markers_and_component_counts(self):
        for marker in (0xc0, 0xc1, 0xc2):
            for components in (1, 3, 4):
                with self.subTest(marker=marker, components=components):
                    self.assertEqual(jpeg_dimensions(jpeg_header(640, 400, components, marker)),
                                     (640, 400, components))

    def test_orientation_byte_orders_and_all_values(self):
        for order in ('II', 'MM'):
            for value in range(1, 9):
                data = b'\xff\xd8' + exif_segment(value, order) + jpeg_header()[2:]
                self.assertEqual(exif_orientation(data), value)

    def test_missing_garbage_and_invalid_orientation(self):
        for app1 in (b'', segment(0xe1, b'not Exif'), segment(0xe1, b'Exif\0\0bad'),
                     exif_segment(0), exif_segment(9)):
            self.assertEqual(exif_orientation(b'\xff\xd8' + app1 + jpeg_header()[2:]), 1)
        for data in (b'', b'\xff\xd8\xff', b'\xff\xd8\xff\xe1\xff\xff',
                     b'\xff\xd8' + exif_segment(6)[:-5]):
            self.assertEqual(exif_orientation(data), 1)

    def test_malformed_tiff_offsets_and_counts(self):
        payloads = [b'II\x2a\0\xff\xff\xff\xff', b'MM\0*\0\0\0\x08\xff\xff',
                    b'II\0\0\x08\0\0\0', b'II\x2a\0\0\0\0\0']
        for payload in payloads:
            data = b'\xff\xd8' + segment(0xe1, b'Exif\0\0' + payload) + jpeg_header()[2:]
            self.assertEqual(exif_orientation(data), 1)

    def test_bad_segment_bounds_fail(self):
        for data in (b'', b'\xff\xd8', b'\xff\xd8\xff', b'\xff\xd8garbage',
                     b'\xff\xd8\xff\xc0\0\x01', b'\xff\xd8\xff\xc0\xff\xff',
                     b'\xff\xd8' + segment(0xc0, b'\x08\0')):
            with self.assertRaises(Error):
                jpeg_dimensions(data)


class CardImageIntakeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='card-intake-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_generated_jpeg_preserved_even_without_jpeg_extension(self):
        source = self.root / 'landscape.jpg'
        generate_image(source)
        data = source.read_bytes()
        source = source.rename(self.root / 'image.unknown')
        image = load_card_image(source, self.root)
        self.assertEqual((image.width, image.height, image.orientation), (1024, 648, 1))
        self.assertEqual(jpeg_dimensions(data), (1024, 648, 3))
        self.assertEqual(image.jpeg, data)

    def test_exif_jpeg_is_not_reencoded(self):
        source = self.root / 'oriented.jpg'
        generate_image(source)
        data = source.read_bytes()
        source.write_bytes(data[:2] + exif_segment(6) + data[2:])
        image = load_card_image(source, self.root)
        self.assertEqual(image.orientation, 6)
        self.assertEqual(image.jpeg, source.read_bytes())

    def test_png_converts_through_sips(self):
        source = self.root / 'portrait.png'
        generate_image(source, '648x1024')
        image = load_card_image(source, self.root)
        self.assertEqual((image.width, image.height), (648, 1024))
        self.assertEqual(jpeg_dimensions(image.jpeg), (648, 1024, 3))
        self.assertEqual(list(self.root.iterdir()), [source])

    def test_truncated_file_rejected(self):
        source = self.root / 'truncated.jpg'
        source.write_bytes(b'\xff\xd8\xff\xc0\xff\xff')
        with self.assertRaises(Error):
            load_card_image(source, self.root)

    def test_incomplete_scan_and_pixel_limit_rejected_before_conversion(self):
        source = self.root / 'incomplete.jpg'
        generate_image(source)
        source.write_bytes(source.read_bytes()[:-2])
        with patch('card_images.media.run') as run:
            with self.assertRaisesRegex(Error, 'missing end marker'):
                load_card_image(source, self.root)
            source.write_bytes(jpeg_header(20000, 20000))
            with self.assertRaisesRegex(Error, 'megapixel limit'):
                load_card_image(source, self.root)
            run.assert_not_called()

    def test_nonregular_missing_and_oversize_rejected(self):
        for source in (self.root, self.root / 'missing.jpg'):
            with self.assertRaises(Error):
                load_card_image(source, self.root)
        source = self.root / 'oversize.jpg'
        with source.open('wb') as file:
            file.truncate(MAX_IMAGE_BYTES + 1)
        with patch('card_images.media.run') as run:
            with self.assertRaisesRegex(Error, 'exceeds'):
                load_card_image(source, self.root)
            run.assert_not_called()

    def test_cmyk_uses_conversion_and_rejects_remaining_cmyk(self):
        source = self.root / 'cmyk.jpg'
        source.write_bytes(jpeg_header(components=4))
        def converted(args):
            Path(args[args.index('--out') + 1]).write_bytes(jpeg_header())
        with patch('card_images.media.run', side_effect=converted) as run:
            self.assertEqual(jpeg_dimensions(load_card_image(source, self.root).jpeg)[2], 3)
            self.assertEqual(run.call_args.args[0][0], '/usr/bin/sips')
        def unchanged(args):
            Path(args[args.index('--out') + 1]).write_bytes(source.read_bytes())
        with patch('card_images.media.run', side_effect=unchanged):
            with self.assertRaisesRegex(Error, 'CMYK'):
                load_card_image(source, self.root)
