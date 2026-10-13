# Repository guidance

This checkout owns the code bundled into `build/Performance Audio.app`:
Performance Audio, Convert Video, Custom Conversion, BPM Over Time and Rhythm
Practice. Preserve the sibling `scripts/` and `build/` layout; source modules
resolve their runtime paths relative to it.

## Repository map and contracts

- `scripts/conversion_settings.py` owns custom media-conversion settings;
  `conversion_jobs.py` reuses the audio/video engines and stores user presets.
- `scripts/conversion_editor.py` serves a short-lived loopback editor for
  BTT-selected files, with its HTML/CSS/JS siblings. It is not an always-on service.
- Dolby Vision audio replacement uses MP4 with lossless ALAC unless AAC is
  explicitly selected; `scripts/performance_audio.py` validates preserved
  video/HDR metadata before publication.
- `scripts/media_selection.py` owns Finder/picker selection provenance;
  Performance Audio and Convert Video's interactive paths both open the
  confirmation editor.
- `scripts/media_task_control.py` owns the job-state wire contract and
  cancellation of an owned process group; `conversion_worker.py` runs foreground
  batch jobs.
- `scripts/conversion_storage.py` atomically publishes recipes/progress.
  Completed outputs are recorded at the publisher boundary before cancellation
  can interrupt reporting.
- `scripts/media_formats.py` owns container choices; `video_export.py` owns
  resize/remux preservation, including decoded frame-timing validation and Dolby
  Vision profile 8 MP4 encoding. SDR conversion stays in `convert_video.py`.
- `scripts/conversion_reports.py` stores audio measurements and conversion recipes
  under ignored `build/conversion-reports/`, keyed by captured output generation.
  Job manifests retain report generation IDs; the editor serves only
  authenticated, indexed report requests, independent of whether the video
  still exists.
- `scripts/bpm_over_time.py` and `rhythm_practice.py` share the scientific Python
  environment created by `setup_bpm_analysis.py`; rebuild venvs rather than move
  them. BPM/Rhythm reports remain local and originals are unchanged.
- `scripts/build_performance_audio_app.py` owns the bundle allowlist and pointer
  files. Build to a new path; never mutate a signed bundle in place.
- `applescript/performance_audio.applescript` owns the native Finder droplet.
- `tests/` contains generated-media Python regressions and the pure rhythm-core
  Node tests. Commands are in `README.md`.

## Ownership and safety

BTT tooling stays in `~/dev/scripts/macbook/bettertouchtool/`, including installers,
menu builders, backups, maintenance and `btt_guard`; never restore it here.
Its `btt_common.MEDIA_TOOLS` resolves this checkout using `MAC_MEDIA_TOOLS` or
`~/dev/mac-media-tools`. BTT configuration changes need a verified private backup
and the guarded API route; never write its live SQLite database. Never promote a
known-good BTT checkpoint as an incidental part of a deployment.

Keep private jobs, reports, caches, presets and diagnostics under ignored build
or temporary directories; don't commit personal media or runtime data. Stop and
coordinate if a suite process is running before relocating data. Preserve saved
custom presets and report generation IDs. Test with synthetic fixtures unless
real-media processing is explicitly authorized.

Before adding helpers, inspect the map and existing modules. Shared settings and
wire records have one canonical definition; validate inputs at trust boundaries.
Keep comments about why, not incident stories. Default code-shape review triggers
are 800 lines per file and 80 per function; do not grow already oversized units
or add escape hatches just to pass checks. Carryover baseline exceptions require
review, not silent rebaselining.
