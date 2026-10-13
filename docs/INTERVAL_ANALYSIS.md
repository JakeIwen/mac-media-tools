# Interval Analysis

Media → Tools → Interval Analysis compares the pitch of a reference vocal take
with a second take sung above or below it. Record both in the browser, or choose
audio/video files. Analysis stays on this Mac; originals are never modified.
The tool shares the BPM/Rhythm scientific Python runtime and FFmpeg.

## Install

Complete the [repository setup](../README.md#setup) and build the updated shared
app before installing the BTT item:

```sh
/usr/bin/python3 ~/dev/scripts/macbook/bettertouchtool/install_interval_menu.py --apply
```

The installer uses a stable UUID, verifies a private full Media backup and appends
only its own Tools item. Siblings and their order are preserved; reruns are
idempotent. It matches BPM Over Time's font and height, falling back to the parent
Tools button. Existing correct actions use an appearance-only repair when needed.
It uses BTT's guarded API, never writes the live SQLite database and does not
promote a known-good checkpoint.

If the item exists in BTT configuration but is missing from the open menu, use:

```sh
/usr/bin/python3 ~/dev/scripts/macbook/bettertouchtool/install_interval_menu.py --apply --refresh
```

Then reopen Tools. `--refresh` releases and reopens the Media menu cache; it does
not restart BTT or execute a hovered action. It requires `--apply`.

## Recording workflow

1. Open **Media → Tools → Interval Analysis**. In **Reference take**, choose
   **Record**, allow microphone access and sing the reference phrase. Stop and
   listen back. The level meter and remaining-time display help avoid an empty or
   truncated take. **Choose file…** is the alternative for an existing recording.
2. Wear headphones: the reference must not reach the microphone. Once its playback
   copy is ready, choose **Record while reference plays** in **Comparison take**.
   After the countdown, sing along at the desired interval. Stop manually or let
   capture stop one second after the reference ends, within the capture budget.
3. Choose **Analyze** with **Automatic (recommended)** alignment. Review the
   interval, agreement, warnings, histogram, timeline and segments together.
   Playback delay is only an approximate browser timing hint, not a calibrated
   microphone or headphone latency measurement. Use **Manual offset** and analyze
   again if alignment is wrong.
4. Give the pair a title and notes, then **Save**. Saved sessions appear newest
   first. **Load** restores both takes, the analysis, title and notes;
   **Update notes** updates the loaded title/notes without replacing its audio.
   **Reveal in Finder** opens the saved folder's location. Replacing either take
   clears the current analysis and loaded-session association, not older saves.
   Re-analyzing unchanged takes keeps the title and notes but switches back to
   **Save**, creating a new session rather than changing the older analysis.
5. Choose **Close session** when finished. Unsaved takes live only in the temporary
   work directory and are removed when the server closes. An idle session expires
   after 30 minutes; closing a browser tab is not a save operation.

Each take is limited to 10 minutes; browser uploads are limited to 64 MiB. Capture
reserves headroom for comparison playback startup. Playback uses a decoded
22,050 Hz, mono, 16-bit PCM WAV derivative for recorded, imported and reopened
material alike. Saved sessions retain the original uploaded/imported bytes, not
just this listening derivative. Browser microphone and playback permissions are
required for recording; permission failures appear in the relevant panel.

## Reading the result

- **Interval** is the most frequent stabilised semitone difference where both
  takes are voiced, after alignment. Positive means the comparison is above the
  reference; negative means below; zero is unison. Names retain compound intervals
  such as **Octave + major third**, not only their pitch classes.
- **Agreement** is the percentage of jointly voiced frames assigned to that
  interval. It is not a confidence score or a singing grade. For example, thirds
  that alternate between major and minor can legitimately have low agreement.
- **Median cents** measures the residual from the named interval, using smoothed
  cents on frames assigned to it. There are 100 cents per semitone; `+4 cents`
  means the measured comparison/reference ratio is four cents higher than that
  nominal signed interval. It does not measure tuning against concert pitch.
- **Alignment offset** is in seconds. A positive `+0.14 s` means a matching event
  occurs 0.14 seconds later in the comparison file; comparison time `t` is placed
  at reference time `t - 0.14`. A negative offset means the comparison leads.
  The timeline uses reference time.
- **Alignment mode/confidence** describes timing only. Automatic mode searches
  voicing landmarks within ±0.75 seconds of the measured playback-delay hint.
  Confidence (0–1) describes how distinct that timing match is, not pitch accuracy.
  It also compares competing timing peaks at least 0.10 seconds apart: a rival
  within 20% of the best score makes the search inconclusive. An inconclusive
  search keeps the hint, reports mode **hint**, confidence zero and a warning;
  competing timings are noted when found. A peak at the search boundary also
  warns. **Manual offset**
  bypasses search, accepts −5 to +5 seconds and starts from the last automatic
  result; its confidence of 1 marks a chosen offset, not measured certainty.
- **Ignoring octaves** folds every stabilised label independently before finding
  its own most common interval and agreement. Thus a mixture of major thirds and
  octave-plus-thirds can have a different answer from the full-interval mode.
  Exact octaves fold to unison; above/below direction otherwise remains.
- **Histograms** count stabilised interval labels, not instantaneous FFT peaks.
  The timeline shows the aligned pitch contours plus raw and smoothed interval
  cents, with missing data where either take is unvoiced. **Segments** identify
  stable interval runs lasting at least 0.25 seconds; matching labels can merge
  across unvoiced gaps shorter than 0.30 seconds.

Less than 0.5 seconds of overlapping voiced audio produces no interval answer.
Warnings flag less than two seconds of overlap, almost-unvoiced takes, uncertain
alignment or agreement below 60%. Listen to the recordings rather than treating
any one number as a verdict.

## Method and limitations

Pitch is tracked with `librosa.pyin` (probabilistic YIN), a fundamental-frequency
estimator. The loudest FFT bin is not reliably the sung note: a voice's strongest
spectral peak is often a harmonic, which would create octave or fifth errors.
Analysis uses 22,050 Hz mono audio, 2,048-sample frames, a 256-sample hop
(about 11.6 ms) and a 65–1,100 Hz pitch range.

Alignment uses voiced/unvoiced timing and voicing boundaries, not the musical
interval it is trying to report. Within each contiguous jointly voiced passage,
a 0.25-second running median reduces independent vibrato, a 60-cent hysteresis
threshold prevents rapid label switching, and a label run shorter than
0.10 seconds is absorbed only by an immediately adjacent run that was already at
least 0.10 seconds long (the preceding one when both neighbours qualify). A chain
of short runs, such as a sweep or trill, stays varied, so it lowers agreement and
raises the inconsistency warning instead of collapsing into one interval.
Smoothing never bridges missing audio.
These stabilised labels drive agreement, histograms and segments.

Both takes should be **monophonic singing**: one clear voice per recording,
without chords, accompaniment or reference playback leaking into the microphone.
Breathy, quiet or low input can cause missed voicing or octave errors even with
pYIN. Fast ornaments and brief interval changes may be smoothed away; the frame
hop is not an accuracy guarantee. The octave-insensitive result can help inspect
register differences but does not prove that an octave error occurred.

Imported takes have no measured playback hint. A large start-time difference may
need a manual offset; a fixed offset cannot correct different tempos or timing
that drifts between takes. Repeated note timings can create competing alignment
peaks, especially with tempo drift; the hint is kept when those peaks are too
similar, without choosing whichever offset yields a familiar interval.
Continuous tones with few voicing landmarks can also make automatic alignment
uncertain. This is an offline interval comparison,
**not a live tuner**, score transcription or polyphonic harmony detector.

## CLI

From the repository root, no files opens the recording page:

```sh
cd ~/dev/mac-media-tools
build/bpm-venv/bin/python -B scripts/interval_analysis.py
```

For file analysis, supply exactly two paths, reference first and comparison
second. Replace the example filenames with your own:

```sh
build/bpm-venv/bin/python -B scripts/interval_analysis.py reference.wav comparison.wav
build/bpm-venv/bin/python -B scripts/interval_analysis.py reference.wav comparison.wav --json
build/bpm-venv/bin/python -B scripts/interval_analysis.py reference.wav comparison.wav --offset 0.14
build/bpm-venv/bin/python -B scripts/interval_analysis.py reference.wav comparison.wav --save --title 'Chorus fifth' --notes 'Headphones; second attempt'
```

File mode prints the interval, direction, semitones, agreement, residual cents,
alignment and warnings, or a JSON analysis with `--json`. `--save` keeps both takes
and the result; `--title` and `--notes` supply its metadata. `--audio-track N`
selects the zero-based audio-stream index in both files (default 0).
`--sessions-dir PATH` chooses a different saved-session directory.

In page mode, `--background` starts a detached session with a job log;
`--no-open` suppresses opening the browser and leaves the session URL in stdout
(or the background log). Treat that URL as private: its fragment contains the
session access token. The server binds loopback only and requires that token for
recordings, analysis and session operations.

## Saved data

Private runtime data lives under ignored `build/`:

- `build/interval-analysis/sessions/<timestamp>-<title-slug>/`: original reference
  and comparison files plus `session.json`, containing the title, notes, creation
  time, recording metadata and analysis. Title/notes limits are 120/5,000 characters.
- `build/interval-analysis-jobs/`: background launch logs.
- `build/bpm-cache/`: shared scientific-library cache.

Saved recordings are user data, not disposable cache. Preserve the entire session
folder when backing up; do not commit recordings, metadata or logs. Session
metadata is published atomically after audio copies are complete. Malformed
sessions appear as errors rather than preventing other sessions from loading;
symlinked session directories/files are refused. No cloud service is involved.

## Validation

Run from the repository root after setup. Fixtures are synthetic; these tests do
not process personal recordings or modify live BTT configuration.

```sh
PYTHONPATH=scripts build/bpm-venv/bin/python -B -m unittest discover -s tests -p 'test_interval_*.py'
PYTHONPATH=scripts build/bpm-venv/bin/python -B -m unittest discover -s tests -p test_loopback_session.py
PYTHONPATH=scripts build/bpm-venv/bin/python -B -m unittest discover -s tests -p test_bpm_over_time.py
node --test tests/test_interval_ui.cjs
node --check scripts/interval_analysis.js
node --check scripts/interval_charts.js
node --test ~/dev/scripts/macbook/tests/test_tools_menu_item.cjs
```

The regressions cover known intervals with independent vibrato, landmark alignment,
octave folding, silence, duration-less browser recordings, authenticated routes,
bounded uploads, session round trips and malformed/symlinked storage. Test commands
validate the implementation; actual microphone permissions, headphone isolation
and perceived playback latency still need a local recording check.
