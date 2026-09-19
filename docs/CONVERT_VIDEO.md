# Convert Video

One **Media → Tools → Convert Video** button creates MP4 sharing copies. Format
and resolution belong to the same operation, so there is no separate resize tool.
This version outputs MP4; FFmpeg handles MOV, MP4, and other supported input files.

## Install the button

The converter is already bundled with the local Performance Audio app (version
1.6), including FFmpeg. Run in your normal Terminal:

```sh
/usr/bin/python3 ~/dev/scripts/macbook/bettertouchtool/install_convert_video_menu.py --apply
```

The installer saves and reads back a private **full Media menu backup** before
making changes. It adds one button under the existing Tools submenu, verifies its
action, and preserves other entries/order. Repeating it does not duplicate the
button. No direct BTT database edits. `--inspect` reports the installed state.
The agent's sandbox cannot connect to BTT's AppleEvents interface, so installation
and actual desktop picker behavior must be checked in your Terminal session.

To align the three Tools action buttons with the parent Tools button and place
Convert Video between Performance Audio and Strip Metadata:

```sh
/usr/bin/osascript -l JavaScript ~/dev/scripts/macbook/bettertouchtool/fix_tools_menu.js --apply
```

This reads the live parent height/font (currently 40 px / 25 pt), uses single-line
labels with enough width, and puts Back first. It backs up the complete Media
export before changing only Tools sizing/text/order. Existing actions, colors,
other menus, and the disabled legacy submenu are preserved. `--inspect` previews
the planned changes without applying them; rerunning an already-matching menu is
a no-op.

## Use

Select video files in Finder, then click **Tools → Convert Video**. If no MOV/MP4/M4V
files are selected, a file picker opens. Choose:

| Resolution | Landscape limit | Portrait limit |
| --- | --- | --- |
| Original | Original display dimensions | Original display dimensions |
| 1080p (default) | 1920 × 1080 | 1080 × 1920 |
| 720p | 1280 × 720 | 720 × 1280 |
| 540p | 960 × 540 | 540 × 960 |

The video fits inside the chosen bounds, retaining its aspect ratio with even
pixel rounding. It is never cropped or upscaled. Square/non-16:9 clips keep their
shape. "Original" means unchanged display resolution, not necessarily unchanged
encoding. Anamorphic pixels are normalized to square pixels if video is encoded.

Conversion runs in the background and reveals completed files in Finder. Output
names are `name-mp4-1080p.mp4`, etc. Existing results get numbered suffixes. Originals
remain untouched. The log says whether each stream was copied or encoded.

Open the job logs if a conversion fails:

```sh
open "$HOME/dev/scripts/macbook/build/video-conversion-jobs"
```

## Quality and Instagram

- Compatible **AAC-LC mono/stereo audio at up to 48 kHz is copied**, with no new
  lossy encoding—even when the video is resized. No EQ, compression, normalization,
  ambience, or reverb is applied by the converter.
- PCM/WAV-style soundtracks (including Performance Audio's lossless MOV outputs),
  ALAC, and other audio are encoded **once to AAC-LC at 320 kb/s, 48 kHz**. This is
  high quality, but not lossless. Retain the original as your audio master.
- Suitable H.264 8-bit SDR video is copied when it already fits the chosen bounds,
  has a supported frame rate, and has a reported bitrate at or below 25 Mb/s.
  Your inspected `IMG_3287 2.MOV` fits this path at Original or 1080p: both its video
  and first stereo AAC track can be copied without re-encoding.
- Otherwise video is encoded as H.264/8-bit/SDR, using CRF 18, the fast software
  preset, a 20 Mb/s video cap, and 30 fps. Resizing uses Lanczos. This can take time
  for long or high-resolution recordings; there is no upload/network processing.
- HLG/PQ HDR is tone-mapped to BT.709 SDR so its color values are actually converted,
  not just relabeled. This intentionally changes the HDR presentation. Compatible
  Dolby Vision base layers can follow this path; unsupported profiles are refused.
- Only the main video and selected audio track are included. The default audio
  selection is the first track; your iPhone's alternate spatial track is omitted.
  `--audio-track` can select another mono/stereo track. Surround downmixing is not
  automatic. Auxiliary Apple data, captions, alternate audio/video tracks, and
  editable Cinematic/Photos metadata are not preserved. Common file metadata may
  still be copied; this is not a metadata stripper.
- MP4's index is moved to the front (`faststart`), and output timing, dimensions,
  orientation, codecs, audio duration/channel count, and source stability are
  checked before atomic exclusive publication. Completed temporary files are
  retained if the final rename fails; their path is printed in the log.

This targets practical local files for manual uploads, not the strict Instagram
publishing API. Meta's [official sample lists MP4/MOV, H.264/HEVC and AAC](https://github.com/fbsamples/reels_publishing_apis/blob/main/insta_reels_publishing_api_sample/README.md#reels-requirements-for-publishing)
and additional API limits, including 128 kb/s audio, size and duration constraints.
This converter favors your source audio quality and does not enforce all those
API-specific limits. Start with 1080p for uploads; 540p is mainly a smaller copy.
Actual Instagram acceptance and its downstream processing have not been tested.

## CLI

The packaged CLI works without installing anything else. Replace the example path:

```sh
python3 "$HOME/dev/scripts/macbook/build/Performance Audio.app/Contents/Resources/convert_video.py" \
  "$HOME/Downloads/IMG_3287 2.MOV" --resolution 1080p --reveal
```

Preview the conversion decisions without creating files:

```sh
python3 "$HOME/dev/scripts/macbook/build/Performance Audio.app/Contents/Resources/convert_video.py" \
  "$HOME/Downloads/IMG_3287 2.MOV" --resolution original --plan
```

Use `--interactive --finder-selection --background --notify --reveal` for the same
flow as the BTT button. `--choose` forces a picker when no paths are provided.
Additional file arguments make a batch. `--output-dir DIR` uses an existing output
folder instead of the source's folder. The source CLI at
`macbook/scripts/convert_video.py` also finds the local app's FFmpeg automatically.

For a fresh checkout, install FFmpeg and build the shared app, or supply standalone
tools using the existing builder's `--bundle-tools DIR` option. See
[Performance Audio setup](PERFORMANCE_AUDIO.md#setup).

## Verification

Generated-media tests verify packet-identical AAC, packet-identical H.264 remux,
PCM-to-AAC conversion, orientation, aspect fit/no upscale, HDR-to-SDR output,
timestamp offsets, silent videos, numbering, and unchanged originals:

```sh
python3 -m unittest discover -s macbook/tests -p test_convert_video.py -v
node --test macbook/tests/test_convert_video_menu.cjs
```
