# Temp Card Printer

Temp Card Printer turns card images into a printable PDF at ID-1 size (85.60 ×
53.98 mm). It lays out one or more copies on letter or A4 paper with optional cut
marks. Processing stays on this Mac, and the original images are unchanged.

## Install

Complete the [repository setup](../README.md#setup). The builder never overwrites
an existing app, so when `build/Performance Audio.app` already exists, build the
updated bundle to a new path and then swap it into place:

```sh
cd ~/dev/mac-media-tools
/opt/homebrew/bin/python3 scripts/build_performance_audio_app.py --output 'build/Performance Audio.next.app'
mv 'build/Performance Audio.app' 'build/Performance Audio.prev.app'
mv 'build/Performance Audio.next.app' 'build/Performance Audio.app'
```

The BTT item runs `temp_card_printer.py` from inside `build/Performance Audio.app`
with the Homebrew Python recorded in its `python-path.txt`.

Then install the BTT item from your normal Terminal:

```sh
/usr/bin/python3 ~/dev/scripts/macbook/bettertouchtool/install_card_printer_menu.py --apply
```

The installer uses BTT's guarded API and preserves the other Media menu entries.
If the item exists in BTT configuration but is missing from the open menu, use:

```sh
/usr/bin/python3 ~/dev/scripts/macbook/bettertouchtool/install_card_printer_menu.py --apply --refresh
```

Then reopen Tools. `--refresh` releases and reopens the Media menu cache; it does
not restart BTT or execute a hovered action. It requires `--apply`.

## Workflow

1. Select the front and back card images in Finder.
2. Open **Media → Tools → Temp Card Printer**.
3. Open the generated `Temp Cards.pdf` in Preview. In the print dialog, choose
   **100%** or **Actual size** so the cards remain at real ID-1 dimensions.
4. Cut on the marks, align the front and back, and tape them together.

Without a Finder selection, the tool opens a file picker. Cancelling the picker
exits without creating a PDF.

## CLI

Run from the repository root:

```sh
/opt/homebrew/bin/python3 scripts/temp_card_printer.py front.heic back.heic
/opt/homebrew/bin/python3 scripts/temp_card_printer.py front.heic back.heic --page a4
/opt/homebrew/bin/python3 scripts/temp_card_printer.py front.heic back.heic --copies 2
```

Use `--output PATH` to choose the PDF path, `--no-cut-marks` to omit the marks,
`--scale X` to adjust card size deliberately, `--no-open` to leave Preview
closed, and `--json` for machine-readable output. `--finder` reads the current
Finder selection.

## Behavior

- Every card is placed at the ID-1 dimensions of 85.60 × 53.98 mm, a 1.586:1
  ratio. Portrait images rotate 90 degrees before fitting.
- Images are center-cropped to the card ratio. The tool does not stretch them.
- Cards fill a page grid based on the selected letter (default) or A4 page size;
  additional cards continue on another page.
- The default output is `Temp Cards.pdf` beside the first image. Existing files
  are preserved by adding a number to the new name.
- Originals are never modified. HEIC and PNG input is converted locally with
  `/usr/bin/sips`; the PDF embeds JPEG images.
- No network service is used.

## Troubleshooting

### The picker was cancelled

No output is expected. Run the command again or select the images in Finder before
using the BTT item.

### The printed image is blurry

Use the highest-resolution source available. A small or heavily compressed image
can be enlarged to ID-1 dimensions, but detail cannot be recovered.

### The card prints at the wrong size

Disable **Scale to Fit** and print at **100%** or **Actual size** in Preview. Check
that the printer driver has not added another scaling setting. `--scale` changes
the intended dimensions, so omit it for standard ID-1 output.
