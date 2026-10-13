# Mac media tools

Local Performance Audio, Convert Video, Custom Conversion, BPM Over Time and
Rhythm Practice tools. Sources stay in `scripts/`; the shared Finder app and
private runtime data stay in ignored `build/` beside it.

## Setup

Run from this checkout. The source tools require FFmpeg/ffprobe and Python;
BPM and Rhythm Practice use a separate scientific Python environment.

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
```

## Workflows

- [Performance Audio](docs/PERFORMANCE_AUDIO.md): effects, GarageBand preparation,
  lossless soundtrack replacement and measurements.
- [Convert Video](docs/CONVERT_VIDEO.md): resize/preservation and explicit SDR export.
- [Custom Conversion](docs/CUSTOM_CONVERSION.md): confirmation editor and saved recipes.
- [BPM Over Time](docs/BPM_OVER_TIME.md): local rolling-tempo analysis.
- [Rhythm Practice](docs/RHYTHM_PRACTICE.md): editable timing and listening reports.

Original media is never overwritten. New outputs are numbered, and preservation
checks run before publication. The editor is a short-lived authenticated loopback
session, not an always-on service.

## BetterTouchTool integration

BTT tooling remains in `~/dev/scripts/macbook/bettertouchtool/`, including menu
installers, configuration backups and `btt_guard`. Its `btt_common.MEDIA_TOOLS`
uses this checkout by default; set `MAC_MEDIA_TOOLS` when generating commands for
another location. Do not copy BTT configuration or checkpoints into this repo.
See the [BTT tooling index](../scripts/macbook/bettertouchtool/README.md).

## Data

Keep `build/custom-conversion/` (saved presets and job manifests) and
`build/conversion-reports/` (generation-keyed reports). Job/log directories are
`performance-audio-jobs`, `video-conversion-jobs`, `conversion-editor-jobs`,
`bpm-analysis-jobs` and `rhythm-analysis-jobs`; analysis caches are in `bpm-cache`.
These private files are ignored, as are the app and `bpm-venv`. Report generation
IDs must survive a move; do not regenerate them from changed media paths.
Editable audio defaults live in `scripts/performance_audio_presets.json`.

## Tests

Tests use generated tiny media fixtures, not personal recordings. They need
`ffmpeg-full` (or `FFMPEG`/`FFPROBE` set to equivalent executables).

```sh
PYTHONPATH=scripts build/bpm-venv/bin/python -B -m unittest discover -s tests
node --test tests/test_rhythm_core.cjs
```

BTT installer tests remain in the scripts repository. The code-shape baseline
was carried over from that repository with only these files retained and paths
adjusted; it was not rebaselined during extraction.
