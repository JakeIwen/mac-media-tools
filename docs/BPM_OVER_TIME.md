# BPM over time

**Tools → BPM Over Time** analyzes an audio or video file and plots 60 rolling
tempo estimates over its entire duration. It does not change the source file.
Analysis and reports stay local; there are no model downloads or uploads.

## Add the Tools button

The analyzer and its local Python environment have already been installed in this
checkout. Run this in your normal Terminal to add the BTT button:

```sh
/usr/bin/python3 ~/dev/scripts/macbook/bettertouchtool/install_bpm_menu.py --apply
```

It appends after the existing Tools items, using their shared 40 px/25 pt styling.
The installer verifies a private full Media backup before any mutation, uses
complete `get_trigger` exports, and only creates/updates its own button. Existing
actions and ordering are preserved. `--inspect` checks without changing BTT.
Agent-side AppleEvents are blocked by the macOS execution sandbox; the live
installation and native Finder/file-picker interaction require your desktop session.

## Everyday use

Select one or more audio/video files in Finder and click **Tools → BPM Over Time**.
If no supported media is selected, a file picker opens. This accepts MOV/MP4 and
common audio formats such as WAV, AIFF, M4A, MP3 and FLAC. Explicit CLI paths can
use other FFmpeg-supported formats.

The job runs in the background and opens an offline HTML report. Each source gets
a neighboring `filename-bpm` folder, containing:

- `bpm-over-time.html`: graph with hover/focus details and half/double-tempo views.
- `bpm-over-time.png` and `.svg`: standalone graph images for sharing/export.
- `bpm-data.csv`: exactly 60 rows by default, including missing estimates.
- `analysis.json`: settings, measurements, competing candidates and raw local winners.

Repeated runs create numbered report folders. Existing reports and media are
never overwritten. If report creation fails, any partial report folder is kept
and identified in the log. The HTML contains all chart/data content inline and
requires no local webserver or internet connection.

## What each point means

The file is divided into 60 equal time intervals. One data point is placed at
each interval's center. Its BPM estimate uses a centered, overlapping window:

`window seconds = max(8, 2 × file duration / 60)`

The window is capped at the file length and shifted inward at the beginning/end
so it stays inside the recording. For a six-minute file, windows are about 12
seconds long and points about 6 seconds apart. The exact interval is available
in each tooltip and CSV row. Files shorter than the window use the same full-file
window for every point; the report explicitly flags that repetition.

This estimates periodicity over each window rather than averaging incompatible
BPM readings (for example, averaging 75 and 150 into a misleading 112.5 BPM).
The signal is decoded to temporary 22.05 kHz mono audio, then analyzed using
[librosa's spectral-flux onset strength](https://librosa.org/doc/0.11.0/generated/librosa.onset.onset_strength.html).
The revised tracker tests a dense 45–220 BPM grid (240 bins per octave). Each
tempo is scored using its beat period **and the next three repetitions**, which
helps when grouped strums make single-beat evidence weak. A dynamic-programming
trajectory uses a squared log-tempo transition cost scaled by elapsed time. This
discourages abrupt beat-grouping switches while allowing acceleration, slowing,
and supported tempo changes. It evaluates evidence at every grid tempo instead
of restricting the path to a few isolated peaks. Unsupported windows may be
bridged internally but remain gaps in the output.

Automatic starting-tempo selection has a broad, weak preference around 120 BPM
only at the beginning. An optional `--bpm-hint` identifies an approximate starting
beat level (within about ±25% in the first supported window). It does **not** fix
the tempo for the rest of the file or prescribe when a change should happen.
The algorithm does not use filenames or an expected acceleration timestamp.

Only the selected audio track is analyzed (first by default). Its start offset
is aligned to the media timeline; video-only sections are represented as silence.
The source's audio quality, encoding, timing and metadata are not modified.

## Read the graph cautiously

- **Blue:** a clearer periodic pulse without a close competing interpretation.
- **Amber:** weaker evidence, competing tempo candidates, or a continuity-selected
  alternative to the independent local winner. A sudden jump can be a change in
  beat grouping rather than an actual change in playing speed.
- **Gap:** insufficient signal, too few onsets, or no adequate periodic match.

The clarity score is a heuristic combined periodicity score, **not a probability or
an accuracy guarantee**. Vocals, strumming subdivisions, triplets, accents, rubato,
background sounds and pauses can confuse a pulse detector. Half/double tempo and
other grouping errors remain possible. Many amber points mean the musical BPM
needs listening/manual confirmation. Adjacent windows overlap, so the points
are not independent observations.

The ½/Double buttons change only the report's displayed interpretation, including
the vertical axis and tooltips. PNG/SVG/CSV/JSON downloads retain the analyzed
values. The CSV keeps the independent multi-period `local_bpm` winner and
`continuity_selected` alongside the plotted trajectory's `bpm`; JSON also includes
local peak scores. No BPM is displayed for silence or insufficient local support.

## CLI and tuning

Run with the prepared scientific Python environment:

```sh
"$HOME/dev/scripts/macbook/build/bpm-venv/bin/python" \
  "$HOME/dev/scripts/macbook/build/Performance Audio.app/Contents/Resources/bpm_over_time.py" \
  "$HOME/Downloads/IMG_3287 2.MOV" --points 60 --open
```

To supply an approximate 140 BPM starting point while allowing later acceleration:

```sh
"$HOME/dev/scripts/macbook/build/bpm-venv/bin/python" \
  "$HOME/dev/scripts/macbook/build/Performance Audio.app/Contents/Resources/bpm_over_time.py" \
  "$HOME/Downloads/IMG_3287 2.MOV" --points 60 --bpm-hint 140 --open
```

`--window-seconds 12` selects a fixed window. `--min-bpm` and `--max-bpm` constrain
the entire track and should leave room for real tempo changes.
Other options: `--output-dir EXISTING_DIR`, `--audio-track 1`, `--choose`,
`--finder-selection`, `--background`, and `--notify`. Parameters are validated;
point counts are 2–1000, BPM search limits 30–300, and explicit windows at least
2 seconds. Very short windows may not contain enough beats for an estimate.
Long files need more memory for the onset analysis; first use can take longer
while the local scientific libraries build caches.

Background logs and caches live outside the signed app bundle:

```sh
open "$HOME/dev/scripts/macbook/build/bpm-analysis-jobs"
```

For a fresh checkout or a removed runtime, set up the local environment and build
an updated app using the existing builder instructions:

```sh
cd ~/dev/scripts
/opt/homebrew/bin/python3 macbook/scripts/setup_bpm_analysis.py
```

`bpm-requirements.txt` pins the main libraries. The environment is under
`macbook/build/bpm-venv`; system packages are not installed or changed by this setup.
The app records this runtime's absolute path, so keep the checkout in place.

## Validation and preview

Tests cover 60/90/120/180 BPM clicks, gradual drift, a tempo step, later acceleration
and slowing despite a starting hint, alternating weak beats, silence,
sustained tones, pauses, video/audio offsets, CSV/graph
exports, special filenames, unchanged originals and no-clobber report folders.
The offline graph was checked with the clean browser: 60 points/rows, hover
details, half/double controls and corresponding axis changes.

```sh
macbook/build/bpm-venv/bin/python -m unittest discover -s macbook/tests -p test_bpm_over_time.py -v
node --test macbook/tests/test_bpm_menu.cjs
```

A preview for the earlier recording was generated without changing it:

```sh
open "$HOME/dev/scripts/tmp/bpm-rework/IMG_3287 2-bpm/bpm-over-time.html"
```

The first implementation incorrectly switched between roughly 73, 98 and 147 BPM
on this recording. The revised **automatic, no-hint** analysis stays mostly around
142–152 BPM until about 5:15, then rises toward 200 BPM. Its closing-window estimates
fall again; these remain estimates rather than a manually verified beat count.
The user's description of a late acceleration was used to assess the result, not
as a hard-coded change point. Earlier reports are retained for comparison.
