"""Synthetic JPEG headers and generated images shared by card-tool tests."""
from pathlib import Path
import struct

import convert_video
import performance_audio as media


def segment(marker, payload):
    return bytes((0xff, marker)) + struct.pack('>H', len(payload) + 2) + payload


def jpeg_header(width=1024, height=648, components=3, marker=0xc0):
    frame = struct.pack('>BHHB', 8, height, width, components)
    frame += b''.join(bytes((index + 1, 0x11, 0)) for index in range(components))
    return b'\xff\xd8' + segment(marker, frame) + b'\xff\xd9'


def exif_segment(orientation, byte_order='II'):
    order = '<' if byte_order == 'II' else '>'
    header = byte_order.encode() + struct.pack(order + 'HIH', 42, 8, 1)
    entry = struct.pack(order + 'HHIH', 0x0112, 3, 1, orientation) + b'\0\0'
    return segment(0xe1, b'Exif\0\0' + header + entry + b'\0' * 4)


def generate_image(path, size='1024x648', pixel_format=None):
    convert_video.configure_tools()
    args = [media.tool('ffmpeg'), '-hide_banner', '-v', 'error', '-nostdin', '-y',
            '-f', 'lavfi', '-i', f'testsrc2=size={size}:duration=0.1', '-frames:v', '1']
    if pixel_format:
        args.extend(['-pix_fmt', pixel_format])
    media.run([*args, Path(path)])
