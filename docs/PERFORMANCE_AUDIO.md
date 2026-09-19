# Performance Audio

Automatically apply saved audio effects to an iPhone video, or extract its
soundtrack for GarageBand and combine your edit with the original video. Runs
locally on the Mac; no uploads or Python packages. Original files are never modified.

## Automatic effects: no GarageBand needed

Open the app, choose **Process automatically with an effects preset**, select a
preset and videos. Or drop videos onto the app and choose a preset once for that
batch. Processing runs in the background; the new video appears beside its source
and is revealed in Finder. A notification reports completion or failure.

```sh
open "$HOME/dev/scripts/macbook/build/Performance Audio.app"
```

Included presets (editable starting points, not settings tuned to your recording):

| Preset | Ambience: level / decay / predelay | Reverb: level / decay / predelay | EQ and compression |
| --- | --- | --- | --- |
| `clean` — Normalize only | Off | Off | Off |
| `subtle` — Subtle room (default) | 8% / 0.22 s / 6 ms | 10% / 0.8 s / 20 ms | 40 Hz high-pass; no compression |
| `fuller` — Fuller room + gentle compression | 12% / 0.30 s / 8 ms | 18% / 1.3 s / 25 ms | 40 Hz high-pass; 1.6:1 at −18 dBFS |
| `jake-preferred` — Jake Preferred | 16.8% / 0.30 s / 8 ms | 25.2% / 1.3 s / 25 ms | 70 Hz high-pass; −2 dB at 250 Hz, +1.5 dB at 3 kHz; approximately 1.88:1 at −18 dBFS |

The effect percentages are independent **return levels relative to dry=100%**,
not fractions of one shared wet/dry budget. Each can be set to zero independently.

Jake Preferred is a starting point for a single recording containing acoustic
guitar and vocals. Its EQ gently reduces low-mid muddiness and adds presence;
both bell bands use Q 0.8. Since there is no universal "typical" setting, **Fuller
is the stated reference** for the requested increases. Compression uses 25% more
steady-state dB attenuation per dB above threshold: `1 − 1/ratio` increases from
0.375 to 0.46875, giving a ratio of about 1.88:1. Reverb return level increases 40%
relative to 18%, yielding 25.2%. Ambience also increases 40%, from 12% to 16.8%;
both effects retain Fuller's decay and predelay settings. The measured result
still depends on performance dynamics, attack/release, the soft knee, and EQ.
This is not a promise of exactly 25% more measured average reduction or 40% more
perceived reverberation. The actual reduction is recorded in each report.

The order is **high-pass/EQ → input level matching → compression → parallel
ambience and reverb → final normalization**. Input matching targets −16 LUFS with a −3 dBTP peak ceiling,
giving the compressor a comparable input level across recordings. Final
normalization targets −16 LUFS with a −1.5 dBTP ceiling after the effects. Both
level adjustments use constant gain. Total positive boost across the two stages
is capped at +18 dB; the final stage gets the unused boost budget. These are
adjustable defaults, not a required delivery standard. A peak or gain limit can
prevent reaching the loudness target, and very quiet input may still receive
little compression. Silence stays silent; material below the loudness gate is
not boosted. Compression remains disabled at ratio 1 (`clean` and `subtle`);
`fuller` and `jake-preferred` use the compressor after input matching.

### Measurements for each video

Every completed automatic-processing video gets two neighboring files:
`video-fuller.mov.audio-report.txt` and `video-fuller.mov.audio-report.json`.
The text report also appears in the Terminal/job log. It includes:

- Measured LUFS and true peak before input matching, after matching, after
  compression, after the combined ambience/reverb mix, and in the final encoded output.
- Input gain and final gain in dB, plus any peak/gain limit preventing the target.
- Compressor mean and maximum attenuation over **50 ms windows**, and the
  percentage of measured time with at least 0.1 dB attenuation.
- An explicit bypass/no-measurable-effect message when appropriate.
- Separate ambience and reverb return levels, decay times, predelays, and bypass states.

Compressor attenuation is calculated from aligned audio immediately before and
after compression, before ambience/reverb or final gain. Each window uses the RMS energy
ratio across the channels. Windows below −80 dBFS input RMS are excluded; the
mean and percentage are duration-weighted across the remaining windows. These
measure actual audio attenuation, not the compressor's instantaneous internal
gain-reduction meter. Silent or unmeasurable levels are `n/a` in text and `null`
in JSON. Reports include the exact preset settings and never overwrite existing
reports. AAC output is measured after encoding, so its peaks may differ slightly.

The extra analysis passes take additional time and use temporary float audio
files, which are cleaned up after processing. Existing BTT buttons use the updated
app engine automatically; no menu reinstall is needed.

Both spatial effects use deterministic synthetic impulse responses and FFmpeg's
[convolution filter](https://ffmpeg.org/ffmpeg-filters.html#afir). **Ambience** uses
short early reflections plus a quiet diffuse tail; **Reverb** uses a longer,
dense decay. Each receives the same compressed audio, and their outputs are added
to the dry signal in parallel: `dry + ambience_mix × ambience + reverb_mix × reverb`.
Changing one amount leaves the other return and the dry path unchanged before
final normalization. Neither effect is passed through the other. Final normalization
can change the gain of the entire combined result. Tails stop at the video's end.

These are not GarageBand plug-ins or exact recreations of its algorithms or knob
scales. Version 1.4 uses independent return levels rather than the earlier single
reverb wet/dry crossfade, so an unchanged numerical reverb setting can sound different.
Older user preset files without ambience settings get ambience=0. `clean` bypasses
both effects. Loudness analysis
uses FFmpeg's [loudnorm measurements](https://ffmpeg.org/ffmpeg-filters.html#loudnorm).

Automatic effects currently support mono/stereo, preserving sample rate and
channel count. Only the selected original audio track is used (first by default).
The video is copied unchanged. The processed audio is stored as 24-bit PCM in MOV,
or AAC in MP4 with `--share`. AAC encoding can change measured peaks slightly.

### BetterTouchTool floating menu

For the existing **Media → Tools** submenu, install the Performance Audio submenu
with the same command style as Strip Metadata:

```sh
/usr/bin/python3 ~/dev/scripts/macbook/bettertouchtool/install_performance_audio_menu.py --apply
```

This queries the live Tools submenu and saves a private JSON backup in
`macbook/build`. **Tools → Performance Audio** becomes a regular button opening
a separate vertical floating menu containing the presets/settings/log buttons.
A top-level menu owns its layout independently of Media's horizontal container;
setting vertical layout fields on the nested submenu did not change its display.
The old nested submenu is retained disabled, with its contents intact, only after
the new menu and launcher pass API readback checks. Strip Metadata and other
entries are preserved. Close and reopen Tools after installing to see the new
button. The separate list closes on selection or an outside click.

Rerunning repairs owned menu/buttons without duplicating them and preserves
user-added entries. It requires an enabled Tools submenu and refuses ambiguous
or conflicting items. Readback checks configuration, not the actual rendered UI.
To inspect the hierarchy without changing BTT:

```sh
/usr/bin/python3 ~/dev/scripts/macbook/bettertouchtool/install_performance_audio_menu.py --inspect
```

The following standalone-menu import is an alternative; it is not required for
the Tools installer.

The generated preset is a **separate** Performance Audio menu plus a named trigger.
It adds no keyboard shortcuts and does not alter the existing Media menu. Import:

```sh
open "$HOME/dev/scripts/macbook/build/Performance Audio.bttpreset"
```

Accept BetterTouchTool's import dialog if shown. That confirmation belongs to
BTT's preset-import UI; the file contains shell actions that run this local tool.
No scripting server needs to be enabled. Then select videos in Finder and show
the menu with:

```sh
open 'btt://trigger_named/?trigger_name=Performance%20Audio'
```

You can also double-click `macbook/build/Performance Audio Menu.command`, or assign
the **Performance Audio** named trigger to an existing BTT button/gesture. The
menu's preset buttons process selected `.mov`, `.mp4`, or `.m4v` files; when
there are none selected, a file picker opens. **Choose files** always opens a
picker and uses the default preset. Other buttons open the settings and job logs.

On first use, macOS may ask the app hosting the script for permission to access
Finder. The OS permission dialog has no supported CLI approval route. If Finder
access is denied, use **Choose files** or pass explicit video paths to the CLI.

This session could not query or install into live BTT: AppleEvents returned an
XPC connection error and `bttcli` reported that its socket server is disabled.
The preset was generated from documented BTT action/menu schemas and the repo's
existing builders; actual import, Finder selection, and menu display still need
verification in the desktop session. No live BTT settings were changed.

### Change the defaults

Both the app UI and BTT read the editable file on each run:

```sh
open -e "$HOME/dev/scripts/macbook/scripts/performance_audio_presets.json"
```

Change `default` to `clean`, `subtle`, `fuller`, or `jake-preferred`; adjust each preset's
`ambience_mix` and `reverb_mix` (each 0–0.5), `ambience_decay_seconds` (0.05–0.8),
`reverb_decay_seconds` (0.1–4), `ambience_predelay_ms` (0–50),
`reverb_predelay_ms` (0–100), high-pass cutoff, compressor settings,
`input_target_lufs`, `input_true_peak_db`, final `target_lufs`/`true_peak_db`, and
the shared `max_boost_db`. Older preset files missing the input settings use
−16 LUFS / −3 dBTP defaults. `ambience_mix: 0.12` means a 12% return gain relative
to the dry signal, independently of `reverb_mix`.
Use ratio `1` to disable compression and cutoff `0` to disable the high-pass.
Optional `eq_bands` is a list of bell filters with `frequency_hz`, `gain_db`, and
`q`; omission means no bell EQ. EQ is applied before input matching and its
settings appear in the text/JSON report. Report JSON version 3 uses
`after_spatial_effects` for the combined mix (formerly `after_reverb`) and includes
a `spatial_effects` routing/return-gain object. The first measurement remains
`after_eq` (formerly `after_highpass` in version 1).
Validation rejects unknown fields and out-of-range settings. Duplicate an entry
under a new lowercase name to add a preset; rebuild/reimport the BTT pack to add
a button (existing buttons read changed settings without reimporting):

```sh
cd ~/dev/scripts
python3 macbook/bettertouchtool/build_performance_audio_menu.py
open "$HOME/dev/scripts/macbook/build/Performance Audio.bttpreset"
```

The signed app bundle also contains fallback defaults for standalone CLI use.
Pass `--config` when using its CLI to read your editable settings. Example with
per-run independent 10% ambience and 20% reverb levels (replace the video path):

```sh
python3 "$HOME/dev/scripts/macbook/build/Performance Audio.app/Contents/Resources/performance_audio.py" \
  process "$HOME/Movies/IMG_1234.MOV" --preset subtle --ambience 10 --reverb 20 \
  --config "$HOME/dev/scripts/macbook/scripts/performance_audio_presets.json"
```

Use `--ambience 0` or `--reverb 0` to bypass only that effect, or both to bypass
the spatial effects entirely. Add `--share` for MP4/AAC, `--audio-track 1` for the second track, or additional
file paths for a batch. Outputs are `IMG_1234-subtle.mov`, then numbered copies.
Background jobs keep logs outside the signed bundle in
`macbook/build/performance-audio-jobs/`; use the menu's **Show processing logs**
button to diagnose a failed job. The corresponding CLI command is:

```sh
open "$HOME/dev/scripts/macbook/build/performance-audio-jobs"
```

## Setup

The Finder app has already been built in this working checkout with FFmpeg and
ffprobe bundled, so **no Homebrew installation is needed to use this app**:

```sh
open "$HOME/dev/scripts/macbook/build/Performance Audio.app"
```

This local bundle uses FFmpeg 9.0.1 Intel binaries from
[evermeet.cx](https://evermeet.cx/ffmpeg/), a distributor linked from
[FFmpeg's download page](https://ffmpeg.org/download.html). They run through
Rosetta, which was verified available on this Mac. The app still uses the existing
Homebrew Python interpreter; it is a local utility, not a standalone redistributable.
Its generated bundle is ignored by Git.

For the **source CLI**, tests, or a **fresh checkout**, install FFmpeg (includes
ffprobe) in your normal Terminal, then build the app if desired:

```sh
cd ~/dev/scripts
brew install ffmpeg
python3 macbook/scripts/performance_audio.py doctor
python3 macbook/scripts/build_performance_audio_app.py
```

You can also run the bundled CLI immediately without installing FFmpeg:

```sh
python3 "$HOME/dev/scripts/macbook/build/Performance Audio.app/Contents/Resources/performance_audio.py" --help
```

The builder refuses to overwrite an existing app. To build an updated copy,
choose another name with `--output`, for example:

```sh
python3 macbook/scripts/build_performance_audio_app.py \
  --output "$HOME/dev/scripts/macbook/build/Performance Audio Updated.app"
```

The app bundles the Python script and uses the Python interpreter that built it.
Rebuild if that interpreter is removed during a Python upgrade. FFmpeg and
ffprobe are found in the app's bundled `bin` directory, on PATH, or in Homebrew's
standard locations; `FFMPEG` and `FFPROBE` can override their executable paths for
CLI use. The builder's optional `--bundle-tools DIR` copies standalone binaries
from that directory into the app. It does not download dependencies, and ordinary
Homebrew binaries are not standalone (they depend on Homebrew libraries).

## Everyday workflow

1. AirDrop/save the original video onto your Mac. Export an **unmodified
   original** from Photos if you want to retain the camera's video encoding.
2. Open Performance Audio and choose **Prepare audio for GarageBand**. Each video
   gets a neighboring `filename.MOV.performance` folder. Dropping a video now
   selects the automatic-effects workflow described above.
3. Import `original.wav` into GarageBand at time zero/bar 1. Apply effects without
   moving or stretching the recording.
4. Export a WAVE or AIFF file, preferably 24-bit, into that same folder with the
   name `edited.wav` or `edited.aiff`.
5. Double-click `Finish.command`. Your lossless result is `finished.mov`.

For fewer steps, start `Auto Finish.command` **before** exporting from GarageBand.
It waits for `edited.*` to stop changing for five seconds and automatically makes
the MOV. Each re-export produces `finished-2.mov`, `finished-3.mov`, etc. Leave its
Terminal window running; Ctrl-C stops it. Watch state is per session, so restarting
the watcher processes the current export again. It runs no background service.

`Make Sharing Copy.command` creates `finished-share.mp4` with 320 kb/s AAC audio.
It still copies the video without encoding, so an HEVC/HDR source remains HEVC/HDR;
this is not an H.264/SDR compatibility conversion. For a large batch you can watch
several project folders in one Terminal command.

The app also accepts a dropped project folder to finish it, or a dropped audio
file located inside a project folder. Use `finish --audio` for an export stored
elsewhere. Automatic discovery requires exactly one `edited` file with a WAV,
AIFF, M4A, CAF, or FLAC extension, ignoring case. Remove extra *edited* formats
from the project or select the one you want explicitly.

Keep the source video in place. You may move the video and its neighboring
project folder together; the manifest also records a relative path. The
double-click commands refer to the script/app that created them, so keep that
script/app in place or use the CLI after moving it.

## GarageBand shortcuts

On **Mac**, you can skip WAV extraction: choose **File → Open Movie** in
GarageBand. It adds the movie's audio to a track at the project start, with video
preview. Apply effects there, export the audio, then use `replace` below.
GarageBand also has **File → Movie → Export Audio to Movie** if its built-in
export presets meet your needs. These scripts give you an explicit lossless-audio
master and video stream copying. See Apple's guides for
[opening movies](https://support.apple.com/guide/garageband/add-a-movie-to-your-project-gbnd8470e7ed/mac)
and [exporting a soundtrack to a movie](https://support.apple.com/guide/garageband/add-the-soundtrack-to-the-movie-gbnd46f9a000/mac).

For audio export on Mac, use **Share → Export Song to Disk**, select WAVE/AIFF,
and include the entire performance starting at zero. Apple's
[export guide](https://support.apple.com/guide/garageband/export-songs-to-disk-or-icloud-gbnd7cbf5ed9/mac)
notes that silence can be trimmed from the beginning/end. Verify that your export
retains the original beginning and length. A duration check catches large length
differences, but cannot detect an edit that moves sound while retaining the same
total duration. If you want the exported level to match your mix, review
**Settings → Advanced → Export projects at full volume** (called Auto Normalize
in some versions); see [Apple's settings guide](https://support.apple.com/guide/garageband/change-advanced-settings-gbnded6e79bc/mac).

Save a reusable GarageBand project with your preferred track effects to speed up
the next recording. Duplicate it per performance and replace its source audio.

If you use GarageBand on **iPhone**, transfer `original.wav` through Files/iCloud
Drive or AirDrop, import it from Files, and export the edited song back to the
Mac project folder. Set the song section length to **Automatic** before importing
so the full recording fits. The processing scripts and app run on the Mac.

GarageBand's import, effects, and export steps use its UI; these scripts do not
provide a CLI for controlling GarageBand.

## CLI

All examples run from `~/dev/scripts`. Replace the example input paths with yours;
you can drag a file from Finder into Terminal to enter its path.

```sh
# Prepare multiple recordings; WAVs retain the original sample rate/channels.
python3 macbook/scripts/performance_audio.py prepare \
  "$HOME/Movies/IMG_1234.MOV" "$HOME/Movies/IMG_1235.MOV" --reveal

# Optional: also make a separate video with no audio tracks.
python3 macbook/scripts/performance_audio.py prepare \
  "$HOME/Movies/IMG_1234.MOV" --silent

# Finish after GarageBand exports edited.wav into the project.
python3 macbook/scripts/performance_audio.py finish \
  "$HOME/Movies/IMG_1234.MOV.performance"

# Or combine any existing video and GarageBand export without preparation.
python3 macbook/scripts/performance_audio.py replace \
  "$HOME/Movies/IMG_1234.MOV" "$HOME/Music/edited.aiff"

# Make a separate MP4 with AAC audio for sharing.
python3 macbook/scripts/performance_audio.py replace \
  "$HOME/Movies/IMG_1234.MOV" "$HOME/Music/edited.aiff" --share

# Watch for exports and finish them automatically.
python3 macbook/scripts/performance_audio.py watch \
  "$HOME/Movies/IMG_1234.MOV.performance" \
  "$HOME/Movies/IMG_1235.MOV.performance"

# Standalone silent-video copy.
python3 macbook/scripts/performance_audio.py strip "$HOME/Movies/IMG_1234.MOV"
```

Run `prepare` once per source; existing project folders are refused. The two
prepare examples above are alternatives. `prepare --out-dir DIR` puts projects
elsewhere. `prepare --audio-track 1` selects the second source audio track
(indices start at zero); the default is the first, not a mix of all tracks.

`replace -o PATH` and `strip -o PATH` set an exact output path and refuse to
overwrite anything there. Default names and `finish` outputs auto-number.

## Fidelity and timing

- The main video stream is copied directly, with no recompression or frame-rate
  conversion. Orientation, common color/HDR fields, and video duration are checked
  before publishing. See [FFmpeg's streamcopy documentation](https://ffmpeg.org/ffmpeg.html#Streamcopy).
- Every original audio track is removed from the result. Only the first audio
  track from your edited file is included; cover art is not mistaken for video.
- Lossless MOV copies PCM WAV/AIFF (including float PCM) and ALAC audio directly.
  Other formats are decoded to integer PCM (24-bit, or 32-bit for higher-depth
  sources). WAV extraction uses 24-bit PCM. Lossless processing cannot restore
  information already discarded by the iPhone's or GarageBand's lossy encoding.
- The GarageBand `prepare`/`replace`/`finish` workflow applies no normalization,
  audio sample-rate override, downmixing, or time stretching. The automatic
  `process` workflow applies the effects and level adjustments documented above.
  Extracted audio preserves its source offset relative to
  the first video frame, with silence padding/end trimming to fit that video.
- Finishing aligns the selected video/audio track starts at zero. It rejects a
  duration difference greater than 0.25 seconds. `--tolerance SECONDS` changes that
  threshold. `--allow-duration-mismatch` keeps both tracks in full, so extra audio
  may play beyond the video; it never uses `-shortest` to silently cut video.
- Output files are validated and then published without overwriting. An export
  that changes during processing is rejected; the watcher retries when it changes
  again. If an export fails while its bytes stay unchanged, re-export it or finish
  manually after correcting the issue.
- On macOS, final publication uses `renamex_np(RENAME_EXCL)`, an atomic move that
  refuses existing destinations. It avoids hard-link creation, which was denied
  during a BTT run in Downloads even though reading the source and writing the
  temporary movie succeeded. If final publication fails, the validated movie is
  retained at the `.performance-…` path printed in the log, rather than discarded.
  Incomplete or invalid renders are still cleaned up. A failed batch now sends
  one failure notification instead of both a completion summary and an error.
- Movie metadata is copied where the MOV/MP4 muxer supports it, including potentially
  location tags. Auxiliary Apple data tracks, Cinematic/depth data, subtitles,
  additional video tracks, and all proprietary Photos editing metadata are not
  preserved. Retain your original as the camera master. This tool is intended for
  ordinary flat performance videos, not spatial/multiview editing.

Tests use generated H.264 and 10-bit HEVC/HLG clips, portrait rotation, multiple
audio tracks, nonzero timestamps, 44.1/48 kHz audio, float PCM, ALAC, partial
exports, and paths containing spaces/quotes. They compare video packet hashes and
decoded lossless audio hashes. Real iPhone Dolby Vision/Photos round trips and
GarageBand UI operation still need checking with a representative recording.

```sh
python3 -m unittest discover -s macbook/tests -p 'test_performance_audio*.py' -v
```
