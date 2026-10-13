"""ID-1 card layout and a small PDF 1.4 serializer, with no subprocess work.

PDF coordinates start at bottom-left; points = millimetres × 72 / 25.4.
Exif maps the JPEG unit square to an upright unit square. Portrait cards then
turn clockwise. Uniform scaling covers the card; the card's clip crops excess.
"""
from dataclasses import dataclass
import math

from card_images import CardImage
from performance_audio import Error

CARD_WIDTH_MM = 85.60
CARD_HEIGHT_MM = 53.98
POINTS_PER_MM = 72 / 25.4
PAGE_SIZES = {'letter': (612, 792), 'a4': (595.276, 841.890)}
PAGE_MARGIN_MM = 10
GUTTER_MM = 4

# Affine (a,b,c,d,e,f): (x,y) -> (a*x+c*y+e, b*x+d*y+f).
EXIF_MATRICES = {
    1: (1, 0, 0, 1, 0, 0), 2: (-1, 0, 0, 1, 1, 0),
    3: (-1, 0, 0, -1, 1, 1), 4: (1, 0, 0, -1, 0, 1),
    5: (0, -1, -1, 0, 1, 1), 6: (0, -1, 1, 0, 0, 1),
    7: (0, 1, 1, 0, 0, 0), 8: (0, 1, -1, 0, 1, 0),
}


@dataclass(frozen=True)
class SheetOptions:
    page: str = 'letter'
    copies: int = 1
    cut_marks: bool = True
    scale: float = 1.0

    def __post_init__(self):
        if not isinstance(self.page, str) or self.page not in PAGE_SIZES:
            raise Error('Page must be letter or a4.')
        if type(self.copies) is not int or not 1 <= self.copies <= 20:
            raise Error('Copies must be an integer from 1 to 20.')
        if (type(self.scale) not in (int, float) or not math.isfinite(self.scale) or
                not 0.5 <= self.scale <= 1.5):
            raise Error('Scale must be between 0.5 and 1.5.')
        if type(self.cut_marks) is not bool:
            raise Error('Cut marks must be true or false.')


@dataclass(frozen=True)
class CardRect:
    x: float
    y: float
    width: float
    height: float


def layout(options, count):
    """Return pages of row-major card rectangles; count already includes copies."""
    if type(count) is not int or count < 0:
        raise Error('Card count must be a nonnegative integer.')
    page_width, page_height = PAGE_SIZES[options.page]
    width = CARD_WIDTH_MM * POINTS_PER_MM * options.scale
    height = CARD_HEIGHT_MM * POINTS_PER_MM * options.scale
    margin, gutter = PAGE_MARGIN_MM * POINTS_PER_MM, GUTTER_MM * POINTS_PER_MM
    columns = int((page_width - 2 * margin + gutter) // (width + gutter))
    rows = int((page_height - 2 * margin + gutter) // (height + gutter))
    if columns < 1 or rows < 1:
        raise Error('Even one card does not fit inside the page margins.')
    left = (page_width - columns * width - (columns - 1) * gutter) / 2
    bottom = (page_height - rows * height - (rows - 1) * gutter) / 2
    grid = [CardRect(left + col * (width + gutter),
                     bottom + (rows - row - 1) * (height + gutter), width, height)
            for row in range(rows) for col in range(columns)]
    return [grid[:min(len(grid), count - start)] for start in range(0, count, len(grid))]


def oriented_size(image):
    """Dimensions after Exif, before the optional landscape turn."""
    return ((image.height, image.width) if image.orientation >= 5
            else (image.width, image.height))


def image_matrix(image, rectangle):
    """Map the JPEG unit square onto a landscape image centered over, and covering, the card."""
    a, b, c, d, e, f = EXIF_MATRICES[image.orientation]
    width, height = oriented_size(image)
    if height > width:
        # Compose a clockwise turn after Exif, not before it.
        a, b, c, d, e, f = b, -a, d, -c, f, 1 - e
        width, height = height, width
    scale = max(rectangle.width / width, rectangle.height / height)
    drawn_width, drawn_height = width * scale, height * scale
    left = rectangle.x + (rectangle.width - drawn_width) / 2
    bottom = rectangle.y + (rectangle.height - drawn_height) / 2
    return (a * drawn_width, b * drawn_height, c * drawn_width, d * drawn_height,
            left + e * drawn_width, bottom + f * drawn_height)


def _numbers(values):
    return ' '.join(f'{value:.6f}'.rstrip('0').rstrip('.') or '0' for value in values)


def _cut_marks(rectangle):
    gap, length = 1.0, 4.0
    commands = ['q 0.5 G 0.3 w']
    for x, horizontal in ((rectangle.x, -1), (rectangle.x + rectangle.width, 1)):
        for y, vertical in ((rectangle.y, -1), (rectangle.y + rectangle.height, 1)):
            commands.append(f'{_numbers((x + horizontal * gap, y))} m '
                            f'{_numbers((x + horizontal * (gap + length), y))} l S')
            commands.append(f'{_numbers((x, y + vertical * gap))} m '
                            f'{_numbers((x, y + vertical * (gap + length)))} l S')
    return '\n'.join([*commands, 'Q'])


def _stream(data, attributes=''):
    return (f'<< /Length {len(data)} {attributes} >>\nstream\n'.encode('ascii') +
            data + b'\nendstream')


def _image_object(image):
    color = 'DeviceGray' if image.components == 1 else 'DeviceRGB'
    return _stream(image.jpeg, f'/Type /XObject /Subtype /Image /Width {image.width} '
                   f'/Height {image.height} /ColorSpace /{color} '
                   '/BitsPerComponent 8 /Filter /DCTDecode')


def _serialize(objects):
    result = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offsets = [0]
    for number, value in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f'{number} 0 obj\n'.encode('ascii') + value + b'\nendobj\n')
    start = len(result)
    result.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode('ascii'))
    for offset in offsets[1:]:
        result.extend(f'{offset:010d} 00000 n \n'.encode('ascii'))
    result.extend((f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n'
                   f'startxref\n{start}\n%%EOF\n').encode('ascii'))
    return bytes(result)


def write_pdf(images: list[CardImage], options: SheetOptions) -> bytes:
    if not images:
        raise Error('At least one card image is required.')
    pages = layout(options, len(images) * options.copies)
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'']
    references = []
    for image in images:
        objects.append(_image_object(image))
        references.append(len(objects))
    sequence = [index for index in range(len(images)) for _ in range(options.copies)]
    page_ids, cursor = [], 0
    for page in pages:
        indices = sequence[cursor:cursor + len(page)]
        commands = []
        for rectangle, index in zip(page, indices):
            clip = _numbers((rectangle.x, rectangle.y, rectangle.width, rectangle.height))
            matrix = _numbers(image_matrix(images[index], rectangle))
            commands.append(f'q {clip} re W n {matrix} cm /Im{index} Do Q')
            if options.cut_marks:
                commands.append(_cut_marks(rectangle))
        objects.append(_stream('\n'.join(commands).encode('ascii')))
        content_id = len(objects)
        resources = ' '.join(f'/Im{i} {references[i]} 0 R' for i in sorted(set(indices)))
        objects.append((f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 '
                        f'{_numbers(PAGE_SIZES[options.page])}] '
                        f'/Resources << /XObject << {resources} >> >> '
                        f'/Contents {content_id} 0 R >>').encode('ascii'))
        page_ids.append(len(objects))
        cursor += len(page)
    kids = ' '.join(f'{number} 0 R' for number in page_ids)
    objects[1] = f'<< /Type /Pages /Count {len(pages)} /Kids [{kids}] >>'.encode('ascii')
    return _serialize(objects)
