#!/usr/bin/env python3
"""Print temporary card copies at ID-1 size on a Letter or A4 PDF sheet."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.dont_write_bytecode = True
import performance_audio as media
from card_images import load_card_image
from card_sheet import PAGE_SIZES, SheetOptions, layout, write_pdf
from media_selection import SelectionKind, select_media


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('images', nargs='*', help='card image paths; otherwise use the picker')
    parser.add_argument('--finder', action='store_true', help='prefer selected Finder images')
    parser.add_argument('--output', type=Path, help='PDF path (numbered if it already exists)')
    parser.add_argument('--page', choices=PAGE_SIZES, default='letter')
    parser.add_argument('--copies', type=int, default=1, help='copies of each image, 1–20')
    parser.add_argument('--no-cut-marks', action='store_true')
    parser.add_argument('--scale', type=float, default=1.0, help='physical size multiplier, 0.5–1.5')
    parser.add_argument('--no-open', action='store_true', help='do not open the finished PDF')
    parser.add_argument('--json', action='store_true', help='print only a JSON success summary')
    return parser.parse_args(argv)


def publish_pdf(data, destination):
    """Stage on the destination filesystem, then atomically claim a numbered name."""
    destination = destination.expanduser().absolute()
    if destination.suffix.lower() != '.pdf':
        raise media.Error('Output must have a .pdf extension.')
    if not destination.parent.is_dir():
        raise media.Error(f'Output directory does not exist: {destination.parent}')
    with tempfile.TemporaryDirectory(prefix='.temp-cards-', dir=destination.parent) as directory:
        temporary = Path(directory) / 'sheet.pdf'
        temporary.write_bytes(data)
        temporary.chmod(0o600)
        candidate, number = destination, 2
        while True:
            try:
                media.move_no_replace(temporary, candidate)
                return candidate
            except FileExistsError:
                candidate = destination.with_name(f'{destination.stem}-{number}{destination.suffix}')
                number += 1


def create_sheet(selection, options, output):
    destination = output or selection.files[0].parent / 'Temp Cards.pdf'
    # Image derivatives are private and disappear whether conversion succeeds or fails.
    with tempfile.TemporaryDirectory(prefix='temp-card-printer-') as directory:
        images = [load_card_image(path, directory) for path in selection.files]
        pdf = write_pdf(images, options)
    published = publish_pdf(pdf, destination)
    cards = len(images) * options.copies
    return {'output': str(published), 'pages': len(layout(options, cards)), 'cards': cards}


def main(argv=None):
    args = arguments(argv)
    try:
        options = SheetOptions(args.page, args.copies, not args.no_cut_marks, args.scale)
        selection = select_media(use_finder=args.finder, paths=args.images, kind=SelectionKind.IMAGE)
        if not selection.files:
            print('No images selected.')
            return 0
        summary = create_sheet(selection, options, args.output)
        if not args.no_open:
            try:
                media.run(['/usr/bin/open', summary['output']])
            except media.Error as error:
                raise media.Error(f"PDF saved at {summary['output']}, but opening it failed: {error}") from error
        print(json.dumps(summary) if args.json else
              f"Saved {summary['cards']} cards on {summary['pages']} page(s): {summary['output']}")
        return 0
    except (media.Error, OSError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
