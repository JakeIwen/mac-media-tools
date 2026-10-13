import math
import re
import unittest
from unittest.mock import patch

import card_sheet as sheet
from tests.card_fixtures import exif_segment, jpeg_header
from performance_audio import Error


def image(width=1024, height=648, orientation=1, components=3):
    data = jpeg_header(width, height, components)
    if orientation != 1:
        data = data[:2] + exif_segment(orientation) + data[2:]
    return sheet.CardImage.from_jpeg(data)


def corners(matrix, rectangle):
    """Map the unit square's top-left, top-right and bottom-left into card-relative coordinates."""
    a, b, c, d, e, f = matrix
    return [(round((a*x+c*y+e - rectangle.x) / rectangle.width, 6),
             round((b*x+d*y+f - rectangle.y) / rectangle.height, 6)) for x, y in ((0, 1), (1, 1), (0, 0))]


class CardLayoutTests(unittest.TestCase):
    def test_grid_and_overflow_for_both_page_sizes(self):
        width = sheet.CARD_WIDTH_MM * sheet.POINTS_PER_MM
        height = sheet.CARD_HEIGHT_MM * sheet.POINTS_PER_MM
        margin = sheet.PAGE_MARGIN_MM * sheet.POINTS_PER_MM
        gutter = sheet.GUTTER_MM * sheet.POINTS_PER_MM
        for page, (page_width, page_height) in sheet.PAGE_SIZES.items():
            columns = math.floor((page_width - 2 * margin + gutter) / (width + gutter))
            rows = math.floor((page_height - 2 * margin + gutter) / (height + gutter))
            capacity = columns * rows
            pages = sheet.layout(sheet.SheetOptions(page=page), capacity + 1)
            self.assertEqual((columns, rows), (2, 4))
            self.assertEqual([len(p) for p in pages], [capacity, 1])
            self.assertEqual(len({r.x for r in pages[0]}), columns)
            self.assertEqual(len({r.y for r in pages[0]}), rows)
            self.assertAlmostEqual(pages[0][0].width, width)
            self.assertGreater(pages[0][0].y, pages[0][-1].y)
            self.assertEqual(pages[0][0].y, pages[0][1].y)
            self.assertAlmostEqual(pages[0][0].x, page_width - pages[0][1].x - width)
            self.assertAlmostEqual(pages[0][-1].y, page_height - pages[0][0].y - height)
            self.assertGreaterEqual(pages[0][-1].y, margin)

    def test_portrait_rotation_and_centered_cover(self):
        rectangle = sheet.layout(sheet.SheetOptions(), 1)[0][0]
        for width, height in ((1024, 648), (648, 1024), (1200, 400), (400, 400)):
            a, b, c, d, e, f = sheet.image_matrix(image(width, height), rectangle)
            # Quarter turns and flips keep axes aligned, so extents are column sums.
            drawn_width, drawn_height = abs(a) + abs(c), abs(b) + abs(d)
            # The drawn image covers the card and overhangs it equally on both sides.
            self.assertGreaterEqual(drawn_width + 1e-6, rectangle.width)
            self.assertGreaterEqual(drawn_height + 1e-6, rectangle.height)
            self.assertTrue(math.isclose(drawn_width, rectangle.width) or
                            math.isclose(drawn_height, rectangle.height))
            left = min(e, e + a, e + c, e + a + c)
            bottom = min(f, f + b, f + d, f + b + d)
            self.assertAlmostEqual(rectangle.x - left, left + drawn_width - rectangle.x - rectangle.width)
            self.assertAlmostEqual(rectangle.y - bottom, bottom + drawn_height - rectangle.y - rectangle.height)
        matrix = sheet.image_matrix(image(648, 1024), rectangle)
        self.assertEqual(matrix[0], 0)
        self.assertLess(matrix[1], 0)
        self.assertGreater(matrix[2], 0)
        self.assertEqual(sheet.oriented_size(image(648, 1024, 6)), (1024, 648))
        self.assertEqual(sheet.image_matrix(image(648, 1024, 6), rectangle), matrix)

    def test_all_exif_matrices_preserve_expected_corners(self):
        # Source top-left/top-right/bottom-left after Exif, in PDF bottom-left coordinates.
        expected = {1: [(0, 1), (1, 1), (0, 0)], 2: [(1, 1), (0, 1), (1, 0)],
                    3: [(1, 0), (0, 0), (1, 1)], 4: [(0, 0), (1, 0), (0, 1)],
                    5: [(0, 1), (0, 0), (1, 1)], 6: [(1, 1), (1, 0), (0, 1)],
                    7: [(1, 0), (1, 1), (0, 0)], 8: [(0, 0), (0, 1), (1, 0)]}
        for orientation, expected_corners in expected.items():
            width, height = (1600, 1000) if orientation < 5 else (1000, 1600)
            # A card with the image's own landscape proportions needs no cropping.
            rectangle = sheet.CardRect(10, 20, 1600, 1000)
            matrix = sheet.image_matrix(image(width, height, orientation), rectangle)
            self.assertEqual(corners(matrix, rectangle), expected_corners, orientation)

    def test_invalid_options_counts_and_too_small_page(self):
        for options in ({'copies': 0}, {'copies': 21}, {'copies': True}, {'copies': 1.5},
                        {'scale': 0.49}, {'scale': 1.51}, {'scale': float('nan')},
                        {'scale': float('inf')}, {'page': 'legal'}, {'cut_marks': 'yes'}):
            with self.assertRaises(Error):
                sheet.SheetOptions(**options)
        self.assertEqual(sheet.layout(sheet.SheetOptions(), 0), [])
        with self.assertRaises(Error):
            sheet.layout(sheet.SheetOptions(), -1)
        with patch.dict(sheet.PAGE_SIZES, {'letter': (50, 50)}):
            with self.assertRaisesRegex(Error, 'does not fit'):
                sheet.layout(sheet.SheetOptions(), 1)
        for scale in (0.5, 1.5):
            self.assertTrue(sheet.layout(sheet.SheetOptions(scale=scale), 10))


class CardPdfTests(unittest.TestCase):
    def test_pdf_structure_xref_pages_color_and_content(self):
        pdf = sheet.write_pdf([image(), image(components=1)], sheet.SheetOptions(copies=5))
        self.assertTrue(pdf.startswith(b'%PDF-1.4'))
        self.assertTrue(pdf.rstrip().endswith(b'%%EOF'))
        start = int(re.search(rb'startxref\n(\d+)', pdf)[1])
        self.assertEqual(pdf[start:start + 4], b'xref')
        xref = pdf[start:].split(b'trailer')[0].splitlines()
        count = int(xref[1].split()[1])
        self.assertEqual(len(xref[2:]), count)
        for number, line in enumerate(xref[3:], 1):
            offset = int(line.split()[0])
            self.assertTrue(pdf[offset:].startswith(f'{number} 0 obj\n'.encode()))
        self.assertIn(b'/Count 2', pdf)
        self.assertEqual(len(re.findall(rb'/Type /Page\b', pdf)), 2)
        images = re.findall(rb'<<[^<>]*/Subtype /Image[^<>]*>>', pdf)
        self.assertEqual(len(images), 2)
        for header, color in zip(images, (b'DeviceRGB', b'DeviceGray')):
            self.assertIn(b'/Filter /DCTDecode', header)
            self.assertIn(b'/ColorSpace /' + color, header)
        self.assertEqual(pdf.count(b' re W n '), 10)
        self.assertEqual(pdf.count(b' Do Q'), 10)
        self.assertIn(b'0.5 G 0.3 w', pdf)
        self.assertEqual(pdf.count(b' l S'), 80)
        self.assertNotIn(b'0.5 G 0.3 w', sheet.write_pdf([image()], sheet.SheetOptions(cut_marks=False)))

    def test_cut_marks_stay_outside_cards(self):
        pdf = sheet.write_pdf([image()], sheet.SheetOptions())
        rect = sheet.layout(sheet.SheetOptions(), 1)[0][0]
        lines = re.findall(rb'([-\d.]+) ([-\d.]+) m ([-\d.]+) ([-\d.]+) l S', pdf)
        self.assertEqual(len(lines), 8)
        for line in lines:
            x1, y1, x2, y2 = map(float, line)
            x, y = (x1+x2)/2, (y1+y2)/2
            self.assertFalse(rect.x < x < rect.x + rect.width and rect.y < y < rect.y + rect.height)

    def test_empty_sheet_and_cmyk_image_rejected(self):
        with self.assertRaises(Error):
            sheet.write_pdf([], sheet.SheetOptions())
        with self.assertRaisesRegex(Error, 'CMYK'):
            image(components=4)
