# Mac media tools

Local Performance Audio, Convert Video, Custom Conversion, BPM Over Time,
Rhythm Practice, Interval Analysis and Temp Card Printer tools. Sources stay in
`scripts/`; the shared Finder app and private runtime data stay in ignored `build/`
beside it.

## Setup

Run from this checkout. The source tools require FFmpeg/ffprobe and Python;
BPM, Rhythm Practice and Interval Analysis share a separate scientific Python environment.

```sh
cd ~/dev/mac-media-tools
brew install ffmpeg-full
/opt/homebrew/bin/python3 scripts/setup_bpm_analysis.py
/opt/homebrew/bin/python3 scripts/build_performance_audio_app.py
```

The builder never overwrites an existing app. Use `--output` for a new build.
FFmpeg comes from Homebrew's native keg-only `ffmpeg-full` (`/opt/homebrew/opt/ffmpeg-full/bin`),
found ahead of `PATH`; plain `ffmpeg` lacks the zscale filter HDR conversion needs.
The builder's optional `--bundle-tools DIR` still accepts standalone binaries.
Keep the checkout and build-time Python interpreter in place: the app stores
absolute interpreter, preset and report paths. Recreate, rather than move, venvs.

```sh
open 'build/Performance Audio.app'
/opt/homebrew/bin/python3 scripts/performance_audio.py --help
build/bpm-venv/bin/python scripts/bpm_over_time.py --help
/opt/homebrew/bin/python3 scripts/temp_card_printer.py --help
```

## Workflows

- [Performance Audio](docs/PERFORMANCE_AUDIO.md): effects, GarageBand preparation,
  lossless soundtrack replacement and measurements.
- [Convert Video](docs/CONVERT_VIDEO.md): resize/preservation and explicit SDR export.
- [Custom Conversion](docs/CUSTOM_CONVERSION.md): confirmation editor and saved recipes.
- [BPM Over Time](docs/BPM_OVER_TIME.md): local rolling-tempo analysis.
- [Rhythm Practice](docs/RHYTHM_PRACTICE.md): editable timing and listening reports.
- [Interval Analysis](docs/INTERVAL_ANALYSIS.md): record two vocal takes, compare
  their intervals and save recordings with notes.
- [Temp Card Printer](docs/TEMP_CARD_PRINTER.md): lay out ID-1 card images for
  printing, cutting and taping.

Original media is never overwritten. New outputs are numbered, and preservation
checks run before publication. The editor is a short-lived authenticated loopback
session, not an always-on service.

## Temp Card Printer

Create a PDF of selected card photos/scans (JPEG, PNG, HEIC and other macOS image
formats). No Python image packages or scientific venv are needed; intake uses
macOS `sips`. Exif orientation is corrected, portrait images turn clockwise, and
a centered crop fills ID-1 cards (85.60 × 53.98 mm). Check the crop before printing:
this is not document detection or perspective correction.

```sh
/opt/homebrew/bin/python3 scripts/temp_card_printer.py --finder
/opt/homebrew/bin/python3 scripts/temp_card_printer.py front.jpg back.png --no-open
```

Without paths or `--finder`, a file picker opens. Options include `--page a4`,
`--copies 2` (1–20 of each image, grouped by image), `--no-cut-marks`, `--scale 1.0`
(0.5–1.5), `--output cards.pdf` and `--json`. Default output is `Temp Cards.pdf`
beside the first image, numbered `Temp Cards-2.pdf` and onward without overwriting.
The PDF opens in the default viewer unless `--no-open` is given. At scale 1,
Letter and A4 both fit 2 columns × 4 rows; additional cards spill onto new pages.
Print at **100% / Actual Size**, not “Fit”, to preserve the specified dimensions.
Cards and temporary derivatives stay local; files over 60 MiB or JPEG images
over 100 megapixels are rejected. An explicit output path must end in `.pdf`.

## BetterTouchTool integration

BTT tooling remains in `~/dev/scripts/macbook/bettertouchtool/`, including menu
installers, configuration backups and `btt_guard`. Its `btt_common.MEDIA_TOOLS`
uses this checkout by default; set `MAC_MEDIA_TOOLS` when generating commands for
another location. Do not copy BTT configuration or checkpoints into this repo.
See the [BTT tooling index](../scripts/macbook/bettertouchtool/README.md).

## Data

Keep `build/custom-conversion/` (saved presets and job manifests) and
`build/conversion-reports/` (generation-keyed reports), plus
`build/interval-analysis/sessions/` (private reference/comparison recordings,
notes and analysis metadata). Job/log directories under `build/` are
`performance-audio-jobs`, `video-conversion-jobs`, `conversion-editor-jobs`,
`bpm-analysis-jobs`, `rhythm-analysis-jobs` and `interval-analysis-jobs`; analysis
caches are in `bpm-cache`.
These private files are ignored, as are the app and `bpm-venv`. Report generation
IDs must survive a move; do not regenerate them from changed media paths.
Editable audio defaults live in `scripts/performance_audio_presets.json`.

## Tests

Tests use generated tiny media fixtures, not personal recordings. They need
`ffmpeg-full` (or `FFMPEG`/`FFPROBE` set to equivalent executables).

```sh
PYTHONPATH=scripts build/bpm-venv/bin/python -B -m unittest discover -s tests
node --test tests/test_rhythm_core.cjs
node --test tests/test_interval_ui.cjs
node --check scripts/interval_analysis.js
node --check scripts/interval_charts.js
node --test ~/dev/scripts/macbook/tests/test_tools_menu_item.cjs
```

BTT installer tests remain in the scripts repository. The code-shape baseline
was carried over from that repository with only these files retained and paths
adjusted; it was not rebaselined during extraction.
