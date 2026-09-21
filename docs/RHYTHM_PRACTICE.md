# Rhythm Practice

Tools → Rhythm Practice creates an offline listening and timing report for selected
Finder audio/video files, or opens a picker. It shares the BPM tool's Python runtime
and FFmpeg. Originals are never modified; each run creates a numbered `-rhythm`
folder with HTML, `analysis.json`, and a 192 kb/s AAC listening copy.

Install the BTT item after building the shared app:

```sh
/usr/bin/python3 ~/dev/scripts/macbook/bettertouchtool/install_rhythm_menu.py --apply
```

The installer uses a new stable UUID, verifies a private full Media backup, appends
only its own Tools item, and preserves siblings and order. Reruns are idempotent.
It matches the live BPM Over Time item's font and height (or the parent Tools
button if BPM is absent), including adequate label width and visibility flags.
Existing correct actions are preserved through the appearance-only API.

If an installed item appears in BTT configuration but not in the open menu, repair
its styling and release/reopen the Media menu cache with:

```sh
/usr/bin/python3 ~/dev/scripts/macbook/bettertouchtool/install_rhythm_menu.py --apply --refresh
```

Open Tools again afterward. This does not restart BTT or execute hovered actions.

## Practice workflow

1. Check reference BPM (including half/double interpretation), subdivisions and
   beats per bar. Initial phase is an estimated subdivision alignment, not an
   identified downbeat. Seek to a downbeat and choose **Beat 1 at playhead**.
2. Listen through a 2–60 second section. Blue lines are candidate attacks. Select
   one to move/exclude it; Shift-click the plot or use Add at playhead for misses.
3. Inspect timing deviations and repeated beat positions. Loop a section or review
   bars with larger spread. Enable the reference click as a listening aid.
4. Save session JSON to preserve grid/attack edits. Restore it into the same report.
   Export timing CSV for included attacks under the current reference. HTML changes
   are in memory until exported, not silently persisted to the original report.

This reports timing against a **fixed, editable grid**. Nearest-subdivision offsets
wrap at half the subdivision interval, so they cannot prove a missed/extra whole
beat or quantify accumulated drift. Repeated-position statistics require correct
bar alignment. Missing detections are not missed musical notes. No correctness
score, chord-change inference, or downstroke/upstroke classification is claimed.
Straight, eighth, sixteenth and triplet grids are supported; arbitrary swing and
custom strum/rest patterns are not yet modeled. A drifting or expressive performance
will differ from a fixed grid; align shorter passages to examine local timing.

Detection uses librosa spectral-flux onset peaks (22.05 kHz mono, 1024 FFT,
128-sample hop ~5.8 ms, 90 ms minimum peak spacing). This can split strums or miss
quiet upstrokes, especially with vocals. Attack strength measures relative spectral
change, not calibrated loudness. The listening copy uses the selected original
track with the same offset/silence alignment as analysis, not the mono analysis data.
Reference click uses Web Audio lookahead from audio.currentTime; browser/device
latency is not calibrated, and the click is not used to score recorded timing.

CLI accepts files, `--bpm 90`, `--minimum-gap 0.09` (range .03–.5 s),
`--audio-track 0`, `--output-dir`, `--choose`, `--finder-selection`, `--background`,
`--open` and `--notify`. Run using `macbook/build/bpm-venv/bin/python` and
`macbook/scripts/rhythm_practice.py`. Default BPM is estimated; silence falls back
to an editable 120 BPM reference with no candidate attacks.

## Validation

```sh
macbook/build/bpm-venv/bin/python -m unittest discover -s macbook/tests -p test_rhythm_practice.py -v
node --test macbook/tests/test_rhythm_core.cjs macbook/tests/test_rhythm_menu.cjs
```

Sessions validate source/duration, finite settings, unique event IDs and bounds.
Reported timing has detection uncertainty; sample-grid resolution is not an
accuracy guarantee. All analysis stays local and reports require no web service.
