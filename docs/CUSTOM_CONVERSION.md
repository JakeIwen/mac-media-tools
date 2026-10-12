# Custom conversion

**Tools → Performance Audio → Custom conversion…** opens an editor for the
selected Finder videos, with a file picker fallback. Performance Audio preset
buttons and Convert Video now use this same confirmation screen.

A selected file is never processed immediately: the screen shows its name, full
path, size, duration, HDR status and whether it came from Finder or the picker.
Use **Choose different files** or **Remove**, then **Start processing**. Empty
selections cannot start. Selection revisions and file fingerprints are checked
again when starting so a stale tab cannot process a different selection silently.

While processing, the screen shows the current file, stage, elapsed time and a
bounded detail log. **Cancel processing** stops only that job's owned process group,
allowing cleanup before escalating if necessary. Files completed before cancellation
remain listed. Close and file-selection changes are disabled until the job stops.
There is no percentage/ETA estimate for the multi-pass effects pipeline.
The original recording is never changed. BPM and Rhythm Practice are unchanged.

Load a preset to use as a starting point. Audio sliders show a second preset's
values as amber reference markers; a comparison table lists all presets. Bell EQ
bands are editable by frequency, gain and Q. Input matching, compressor, ambience,
reverb and final normalization reuse the existing audio engine and measurements.

**Start processing** applies the current settings once. **Save as new preset**
stores audio and video settings together for future editor sessions, without
overwriting built-in presets. Saved presets appear in the editor's preset lists;
they do not each create another BTT menu item. Reports are stored in the Git-ignored
`macbook/build/conversion-reports/` directory, with separate generation folders for
each output. The results list links to audio measurements (text/JSON) and the
conversion recipe (JSON), displayed in the page. Videos stay beside their originals;
new report files do not. Existing adjacent reports are not moved or deleted.

The recipe records the exact settings and available audio measurements. Report
references are captured when an output is published, so links still work if that
video is subsequently moved/deleted or its filename reused. Audio measurements
from an audio-plus-SDR job are attached to the final output's report folder.

Video choices:

- **None — preserve original video** is the default. No resizing, frame-rate
  conversion, tone mapping or picture adjustments. The compressed video is copied.
  Processed audio can use lossless PCM in MOV or ALAC in MP4 for Dolby Vision,
  or explicitly selected AAC MP4. With audio effects disabled, the original
  selected audio track is copied, rather than being normalized.
- **SDR sharing** converts to H.264/AAC MP4 at original/1080p/720p/540p size.
  HDR tone mapping is explicit, with Hable, Mobius or Reinhard choices. This can
  change the appearance; no promise of matching QuickTime's HDR display rendering.
- **SDR + picture adjustments** additionally exposes brightness, contrast and
  saturation. These controls require video re-encoding.

Video outputs are new numbered files beside the source. The source is never
overwritten. None retains HDR metadata and fails validation if it is lost.
The bundled FFmpeg MOV muxer drops the Dolby Vision configuration box; the
preservation path therefore writes MP4 with `-strict unofficial`, retaining
Dolby Vision and using lossless ALAC for non-sharing audio. This also fixes the
existing Jake Preferred button on Dolby Vision recordings.

The editor is a per-conversion loopback HTTP session, not an always-on service.
It opens in the default browser and exits after **Close editor session** or 30
minutes without requests when idle. A foreground worker runs each batch; closing
is refused while it runs. Cancellation remains active until owned children stop.
Mutations require an unpredictable session token and same-origin checks. Browser
refresh retains the session in sessionStorage. No uploads or external resources.

Implementation: `conversion_settings.py` is the canonical settings contract;
`conversion_jobs.py` stores immutable saved recipes under
`macbook/build/custom-conversion/` and calls the existing engines;
`conversion_editor.py` and its HTML/CSS/JS siblings own the temporary editor.
Logs live under `macbook/build/conversion-editor-jobs/`. Render intermediates
remain in each job folder for diagnosing failures.

Preset and progress files are atomically published by `conversion_storage.py`.
Malformed saved presets produce a warning without disabling the rest of the
library. A recipe-write failure does not hide a completed video. The shared media
publisher defers SIGINT/SIGTERM over the short rename/status-record boundary so
cancellation cannot hide an already published output from the job manifest.

`install_custom_conversion.py --apply` adds only the editor entry to the existing
Performance Audio dropdown, matches its live font/height, ensures fixed-height
menus fit their content, and verifies all existing entries/actions remain unchanged.
Retries reconcile partial item/layout installs without duplicate rows or repeated growth.
It reuses `btt_common.js` for private verified backups and canonical comparisons.
Do not use the older full-menu installer merely to add this entry.

Validation uses the normal Python test runner with `PYTHONPATH=macbook/scripts`
for `test_conversion_editor.py`, plus `test_custom_conversion_menu.cjs` in Node.
Tests cover controls/settings validation, token/origin checks, independent saved
presets, video packet preservation, explicit picture re-encoding and installer
preservation/idempotence. Real iPhone Dolby Vision packet/metadata validation is
performed against local media without committing that recording.
