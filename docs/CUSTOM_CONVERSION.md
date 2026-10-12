# Custom conversion

**Tools → Performance Audio → Custom conversion…** opens an editor for the
selected Finder videos, with a file picker fallback. Existing preset buttons keep
their one-click behavior.

Load a preset to use as a starting point. Audio sliders show a second preset's
values as amber reference markers; a comparison table lists all presets. Bell EQ
bands are editable by frequency, gain and Q. Input matching, compressor, ambience,
reverb and final normalization reuse the existing audio engine and measurements.

**Convert selected files** applies the current settings once. **Save as new preset**
stores audio and video settings together for future editor sessions, without
overwriting built-in presets. Saved presets appear in the editor's preset lists;
they do not each create another BTT menu item. The `.conversion.json` sidecar
records the exact recipe and available audio measurements.

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

All outputs are new numbered files beside the source. The source is never
overwritten. None retains HDR metadata and fails validation if it is lost.
The bundled FFmpeg MOV muxer drops the Dolby Vision configuration box; the
preservation path therefore writes MP4 with `-strict unofficial`, retaining
Dolby Vision and using lossless ALAC for non-sharing audio. This also fixes the
existing Jake Preferred button on Dolby Vision recordings.

The editor is a per-conversion loopback HTTP session, not an always-on service.
It opens in the default browser and exits after **Close editor session** or 30
minutes without requests when idle. Job threads finish before process exit.
Mutations require an unpredictable session token and same-origin checks. Browser
refresh retains the session in sessionStorage. No uploads or external resources.

Implementation: `conversion_settings.py` is the canonical settings contract;
`conversion_jobs.py` stores immutable saved recipes under
`macbook/build/custom-conversion/` and calls the existing engines;
`conversion_editor.py` and its HTML/CSS/JS siblings own the temporary editor.
Logs live under `macbook/build/conversion-editor-jobs/`. Render intermediates
remain in each job folder for diagnosing failures.

`install_custom_conversion.py --apply` adds only the editor entry to the existing
Performance Audio dropdown, matches its live font/height, grows fixed-height
menus by one row, and verifies all existing entries/actions remain unchanged.
It reuses `btt_common.js` for private verified backups and canonical comparisons.
Do not use the older full-menu installer merely to add this entry.

Validation uses the normal Python test runner with `PYTHONPATH=macbook/scripts`
for `test_conversion_editor.py`, plus `test_custom_conversion_menu.cjs` in Node.
Tests cover controls/settings validation, token/origin checks, independent saved
presets, video packet preservation, explicit picture re-encoding and installer
preservation/idempotence. Real iPhone Dolby Vision packet/metadata validation is
performed against local media without committing that recording.
