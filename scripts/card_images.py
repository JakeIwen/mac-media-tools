"""Bounded image intake, JPEG/Exif parsing and the canonical card image record."""
from dataclasses import dataclass
from pathlib import Path
import stat
import tempfile

import performance_audio as media

MAX_IMAGE_BYTES = 60 * 1024 * 1024
MAX_IMAGE_PIXELS = 100_000_000
# DCTDecode embeds grey and YCbCr JPEGs directly; CMYK needs an inverted Decode array.
EMBEDDABLE_COMPONENTS = (1, 3)


def jpeg_segments(data):
    """Yield header segments, stopping before entropy-coded scan data."""
    if not data.startswith(b'\xff\xd8'):
        raise media.Error('Not a JPEG image.')
    position = 2
    while position < len(data):
        if data[position] != 0xff:
            raise media.Error('Malformed JPEG marker.')
        while position < len(data) and data[position] == 0xff:
            position += 1
        if position >= len(data):
            raise media.Error('Truncated JPEG marker.')
        marker = data[position]
        position += 1
        if marker == 0xd9:
            return
        if marker == 0x01 or 0xd0 <= marker <= 0xd7:
            continue
        if marker in (0, 0xd8) or position + 2 > len(data):
            raise media.Error('Invalid JPEG segment.')
        length = int.from_bytes(data[position:position + 2], 'big')
        if length < 2 or position + length > len(data):
            raise media.Error('Truncated JPEG segment.')
        yield marker, data[position + 2:position + length]
        position += length
        if marker == 0xda:
            return


def jpeg_dimensions(data):
    """Return (width, height, components) for an 8-bit SOF0/1/2 JPEG."""
    for marker, segment in jpeg_segments(data):
        if marker not in (0xc0, 0xc1, 0xc2):
            continue
        if len(segment) < 6:
            raise media.Error('Truncated JPEG frame header.')
        height = int.from_bytes(segment[1:3], 'big')
        width = int.from_bytes(segment[3:5], 'big')
        components = segment[5]
        if (segment[0] != 8 or not width or not height or
                components not in (1, 3, 4) or len(segment) != 6 + 3 * components):
            raise media.Error('Unsupported JPEG frame header.')
        return width, height, components
    raise media.Error('JPEG has no supported frame header.')


def _tiff_orientation(data):
    if len(data) < 8 or data[:2] not in (b'II', b'MM'):
        return None
    order = 'little' if data[:2] == b'II' else 'big'
    number = lambda offset, size: int.from_bytes(data[offset:offset + size], order)
    if number(2, 2) != 42:
        return None
    offset = number(4, 4)
    if offset < 8 or offset + 2 > len(data):
        return None
    count = number(offset, 2)
    start = offset + 2
    if start + count * 12 + 4 > len(data):
        return None
    for entry in range(start, start + count * 12, 12):
        if number(entry, 2) == 0x0112:
            if number(entry + 2, 2) != 3 or number(entry + 4, 4) != 1:
                return None
            value = number(entry + 8, 2)
            return value if 1 <= value <= 8 else None
    return None


def exif_orientation(data):
    """Read TIFF IFD0 Orientation from APP1; absent/broken metadata means 1."""
    try:
        for marker, segment in jpeg_segments(data):
            if marker == 0xe1 and segment.startswith(b'Exif\0\0'):
                orientation = _tiff_orientation(segment[6:])
                if orientation is not None:
                    return orientation
    except media.Error:
        pass
    return 1


@dataclass(frozen=True)
class CardImage:
    """A JPEG whose header has been parsed and checked for direct PDF embedding."""
    jpeg: bytes
    width: int
    height: int
    components: int
    orientation: int = 1

    def __post_init__(self):
        if (not isinstance(self.jpeg, bytes) or
                type(self.width) is not int or type(self.height) is not int or
                self.width <= 0 or self.height <= 0 or
                self.components not in EMBEDDABLE_COMPONENTS or
                type(self.orientation) is not int or not 1 <= self.orientation <= 8):
            raise media.Error('Invalid card image dimensions, orientation or JPEG bytes.')

    @classmethod
    def from_jpeg(cls, data):
        """Validate JPEG bytes once, at the trust boundary."""
        width, height, components = jpeg_dimensions(data)
        if not data.endswith(b'\xff\xd9'):
            raise media.Error('Truncated JPEG: missing end marker.')
        if width * height > MAX_IMAGE_PIXELS:
            raise media.Error('Image exceeds the 100 megapixel limit.')
        if components not in EMBEDDABLE_COMPONENTS:
            raise media.Error('CMYK JPEG is not supported; export an RGB image.')
        return cls(data, width, height, components, exif_orientation(data))


def _read_bounded(path):
    with path.open('rb') as source:
        data = source.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise media.Error(f'Image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB: {path}')
    return data


def _needs_conversion(data):
    """Non-JPEG input and CMYK JPEGs go through sips; a damaged JPEG is an error."""
    return not data.startswith(b'\xff\xd8') or jpeg_dimensions(data)[2] not in EMBEDDABLE_COMPONENTS


def _convert_with_sips(path, workdir):
    with tempfile.NamedTemporaryFile(suffix='.jpg', dir=workdir) as converted:
        media.run(['/usr/bin/sips', '-s', 'format', 'jpeg', '-s', 'formatOptions', 'best',
                   '--out', converted.name, path.resolve()])
        return _read_bounded(Path(converted.name))


def load_card_image(path, workdir) -> CardImage:
    """Keep supported JPEG bytes intact; normalize other inputs with macOS sips."""
    path = Path(path)
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise media.Error(f'Not a regular image file: {path}')
        if info.st_size > MAX_IMAGE_BYTES:
            raise media.Error(f'Image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB: {path}')
        data = _read_bounded(path)
        if _needs_conversion(data):
            data = _convert_with_sips(path, workdir)
        return CardImage.from_jpeg(data)
    except OSError as error:
        raise media.Error(f'Cannot read image {path}: {error}') from error
