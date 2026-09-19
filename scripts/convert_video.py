#!/usr/bin/env python3
"""Make MP4 sharing copies, preserving compatible audio and original files."""

import argparse
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

# Set this before importing bundled helpers: importlib caches a module's code
# before executing that module, which would invalidate the app's signature.
sys.dont_write_bytecode = True

import performance_audio as media
from performance_audio_effects import select_videos


RESOLUTIONS = {"original": None, "1080p": (1920, 1080),
               "720p": (1280, 720), "540p": (960, 540)}


def configure_tools():
    # Source CLI can reuse the already installed local app without Homebrew setup.
    bundled = Path(__file__).resolve().parents[1] / "build/Performance Audio.app/Contents/Resources/bin"
    for name in ("ffmpeg", "ffprobe"):
        if name.upper() not in os.environ and (bundled / name).is_file():
            os.environ[name.upper()] = str(bundled / name)


def ratio(value, default=1):
    try:
        number = float(Fraction(str(value).replace(":", "/")))
        return number if math.isfinite(number) and number > 0 else default
    except (ValueError, ZeroDivisionError):
        return default


def display_size(track):
    width = track["width"] * ratio(track.get("sample_aspect_ratio", "1:1"))
    height = track["height"]
    rotation = media.rotation(track) % 360
    if rotation not in (0, 90, 180, 270):
        raise media.Error("Non-right-angle rotation needs manual review; no conversion performed.")
    return (height, width) if rotation in (90, 270) else (width, height)


def target_size(track, resolution):
    width, height = display_size(track)
    box = RESOLUTIONS[resolution]
    factor = 1
    if box:
        max_w, max_h = box if width >= height else box[::-1]
        factor = min(1, max_w / width, max_h / height)
    # H.264 yuv420p needs even dimensions. Never upscale to reach the chosen cap.
    return max(2, int(width * factor) // 2 * 2), max(2, int(height * factor) // 2 * 2)


def make_plan(info, resolution, audio_track=0):
    video = media.stream(info, "video")
    audio_streams = [s for s in info["streams"] if s["codec_type"] == "audio"]
    audio = media.stream(info, "audio", audio_track) if audio_streams else None
    width, height = target_size(video, resolution)
    transfer = video.get("color_transfer")
    hdr = transfer in ("smpte2084", "arib-std-b67")
    dovi = [d for d in video.get("side_data_list", []) if d.get("side_data_type") == "DOVI configuration record"]
    if dovi and (any(d.get("dv_profile") == 5 for d in dovi) or not hdr):
        raise media.Error("This Dolby Vision stream has no supported HLG/PQ base-layer conversion. "
                          "Export an SDR version first.")
    same_size = (width, height) == display_size(video)
    rate = ratio(video.get("avg_frame_rate"), 30)
    copy_video = (same_size and video.get("codec_name") == "h264" and
                  video.get("pix_fmt") == "yuv420p" and not hdr and
                  video.get("color_primaries") in (None, "unknown", "bt709") and
                  transfer in (None, "unknown", "bt709", "iec61966-2-1") and
                  video.get("field_order") in (None, "unknown", "progressive") and
                  23 <= rate <= 60.01 and int(video.get("bit_rate", 0) or 0) <= 25_000_000)
    if audio and audio.get("channels", 0) > 2:
        raise media.Error("Selected audio is surround/spatial. Choose its stereo companion "
                          "with --audio-track; automatic downmixing is not enabled.")
    copy_audio = bool(audio and audio.get("codec_name") == "aac" and
                      audio.get("profile") == "LC" and int(audio.get("sample_rate", 0)) <= 48000)
    return {"resolution": resolution, "width": width, "height": height,
            "video": video, "audio": audio, "copy_video": copy_video,
            "copy_audio": copy_audio, "hdr_to_sdr": hdr,
            "video_action": "copy H.264 unchanged" if copy_video else "encode H.264 SDR at 30 fps",
            "audio_action": ("no audio" if audio is None else "copy AAC unchanged" if copy_audio
                             else "encode AAC-LC 320 kb/s, 48 kHz (one lossy conversion)")}


def video_filters(plan):
    video = plan["video"]
    filters = []
    if video.get("field_order") not in (None, "unknown", "progressive"):
        filters.append("bwdif=mode=send_frame:parity=auto:deint=all")
    width, height = plan["width"], plan["height"]
    # FFmpeg autorotates before this filter graph. Normalize anamorphic pixels too.
    filters.append(f"scale={width}:{height}:flags=lanczos,setsar=1")
    if plan["hdr_to_sdr"]:
        primaries = video.get("color_primaries")
        matrix = video.get("color_space")
        if primaries in (None, "unknown"): primaries = "bt2020"
        if matrix in (None, "unknown"): matrix = "bt2020nc"
        filters += [f"zscale=pin={primaries}:min={matrix}:tin={video['color_transfer']}:t=linear:npl=100",
                    "format=gbrpf32le", "zscale=p=bt709", "tonemap=tonemap=mobius:param=0.3:desat=2",
                    "zscale=t=bt709:m=bt709:r=tv", "sidedata=mode=delete"]
    elif video.get("color_primaries") not in (None, "unknown", "bt709"):
        # Tagged wide-gamut SDR needs a real conversion, not just new color tags.
        filters.append("zscale=p=bt709:t=bt709:m=bt709:r=tv")
    else:
        filters.append("scale=in_color_matrix=auto:out_color_matrix=bt709:out_range=tv")
    # Use a timestamp-aware fps filter, not output CFR padding that would invent
    # leading frames when the video starts later than its audio.
    filters += ["format=yuv420p", "fps=30"]
    return ",".join(filters)


def validate_conversion(before, plan, result):
    video = media.stream(result, "video")
    if tuple(round(x) for x in display_size(video)) != (plan["width"], plan["height"]):
        raise media.Error("Output dimensions/orientation do not match the requested conversion.")
    if video.get("codec_name") != "h264" or video.get("pix_fmt") != "yuv420p":
        raise media.Error("Output is not the expected H.264 8-bit MP4.")
    if not plan["copy_video"] and media.rotation(video) != 0:
        raise media.Error("Output still has a rotation transform after rotation was applied.")
    if video.get("color_transfer") in ("smpte2084", "arib-std-b67"):
        raise media.Error("HDR signaling remained in the SDR output.")
    if any(s.get("side_data_type") == "DOVI configuration record" for s in video.get("side_data_list", [])):
        raise media.Error("Dolby Vision signaling remained in the SDR output.")
    if abs(media.duration(before, plan["video"]) - media.duration(result, video)) > 0.12:
        raise media.Error("Conversion changed video duration unexpectedly.")
    audio = [s for s in result["streams"] if s["codec_type"] == "audio"]
    if len(audio) != (1 if plan["audio"] else 0):
        raise media.Error("Unexpected number of audio tracks in output.")
    if audio:
        if audio[0].get("codec_name") != "aac" or audio[0].get("channels") != plan["audio"]["channels"]:
            raise media.Error("Output audio codec/channels changed unexpectedly.")
        if abs(media.duration(before, plan["audio"]) - media.duration(result, audio[0])) > 0.12:
            raise media.Error("Conversion changed audio duration unexpectedly.")
        old_offset = float(plan["audio"].get("start_time", 0)) - float(plan["video"].get("start_time", 0))
        new_offset = float(audio[0].get("start_time", 0)) - float(video.get("start_time", 0))
        if abs(old_offset - new_offset) > 0.1:
            raise media.Error("Conversion changed audio/video synchronization unexpectedly.")


def convert(source, resolution="1080p", output_dir=None, audio_track=0, plan_only=False):
    source = source.expanduser().resolve()
    before_stat = media.fingerprint(source)
    info = media.probe(source)
    plan = make_plan(info, resolution, audio_track)
    print(f"{source.name}: {plan['width']} x {plan['height']}; {plan['video_action']}; {plan['audio_action']}", flush=True)
    if plan["hdr_to_sdr"]:
        print("Tone-mapping HDR to SDR for sharing.", flush=True)
    if plan_only:
        return plan
    args = ["-i", source, "-map", f"0:{plan['video']['index']}"]
    if plan["audio"]:
        args += ["-map", f"0:{plan['audio']['index']}"]
    if plan["copy_video"]:
        args += ["-c:v", "copy"]
    else:
        args += ["-vf", video_filters(plan), "-c:v", "libx264", "-preset", "fast",
                 "-crf", "18", "-maxrate", "20M", "-bufsize", "40M",
                 "-fps_mode", "passthrough", "-g", "60", "-x264-params", "open-gop=0",
                 "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709",
                 "-colorspace", "bt709", "-color_range", "tv", "-metadata:s:v:0", "rotate=0"]
    if plan["audio"]:
        args += ["-c:a", "copy"] if plan["copy_audio"] else ["-c:a", "aac", "-b:a", "320k", "-ar", "48000"]
    args += ["-map_metadata", "0", "-map_chapters", "-1", "-movflags", "+faststart"]
    destination = (output_dir.expanduser().resolve() if output_dir else source.parent) / f"{source.stem}-mp4-{resolution}.mp4"

    def validate(result):
        validate_conversion(info, plan, result)
        if media.fingerprint(source) != before_stat:
            raise media.Error("Input changed during conversion; no final output published.")

    result = media.publish(args, destination, validate, numbered=True, preserve_completed=True)
    print(f"Created: {result}", flush=True)
    return result


def notify(message):
    subprocess.run(["/usr/bin/osascript", "-e",
        'on run argv\ndisplay notification (item 1 of argv) with title "Convert Video"\nend run', message],
        capture_output=True)


def choose_resolution():
    script = '''const app=Application.currentApplication(); app.includeStandardAdditions=true;
const choices=['Original resolution','1080p (recommended)','720p','540p (smaller file)'];
const answer=app.chooseFromList(choices,{withTitle:'Convert Video to MP4',
  withPrompt:'Keep the framing and audio quality. Smaller choices only downscale. Compatible AAC is copied; other audio becomes high-quality AAC.',
  defaultItems:['1080p (recommended)']});
JSON.stringify(answer ? ['original','1080p','720p','540p'][choices.indexOf(answer[0])] : null);'''
    return json.loads(media.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", script]))


def run(args):
    if args.background:
        directory = Path(__file__).resolve().parent
        app = next((p for p in directory.parents if p.suffix == ".app"), None)
        log_dir = (app.parent if app else directory.parent / "build") / "video-conversion-jobs"
        log_dir.mkdir(parents=True, exist_ok=True)
        fd, logfile = tempfile.mkstemp(prefix="job-", suffix=".log", dir=log_dir)
        command = [sys.executable, str(Path(__file__).resolve()), "--resolution", args.resolution,
                   "--audio-track", str(args.audio_track)]
        for option in ("interactive", "finder_selection", "choose", "notify", "reveal", "plan"):
            if getattr(args, option): command.append("--" + option.replace("_", "-"))
        if args.output_dir: command += ["--output-dir", str(args.output_dir.expanduser().resolve())]
        command += [str(p.expanduser().resolve()) for p in args.videos]
        with os.fdopen(fd, "w") as log:
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        print(f"Started. Log: {logfile}")
        return
    videos = args.videos
    if not videos and (args.interactive or args.finder_selection or args.choose):
        videos = select_videos(args.finder_selection)
        if not videos: return
    if not videos: raise media.Error("Provide video paths or use --interactive.")
    resolution = choose_resolution() if args.interactive else args.resolution
    if resolution is None: return
    completed, errors = [], []
    for video in videos:
        try:
            result = convert(video, resolution, args.output_dir, args.audio_track, args.plan)
            completed.append(result)
            if args.reveal and not args.plan:
                try: media.run(["/usr/bin/open", "-R", result])
                except media.Error as error: print(f"Saved, but Finder reveal failed: {error}", file=sys.stderr)
        except (media.Error, OSError, ValueError) as error:
            errors.append(f"{video.name}: {error}")
            print(errors[-1], file=sys.stderr, flush=True)
    if errors: raise media.Error(f"{len(completed)} succeeded, {len(errors)} failed.\n" + "\n".join(errors))
    if args.notify and not args.plan: notify(f"Created {len(completed)} MP4 file(s).")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("videos", type=Path, nargs="*")
    parser.add_argument("--resolution", choices=RESOLUTIONS, default="1080p")
    parser.add_argument("--output-dir", type=Path, help="existing output folder; default beside source")
    parser.add_argument("--audio-track", type=int, default=0, help="zero-based audio track; default first")
    parser.add_argument("--interactive", action="store_true", help="show MP4 resolution choices")
    picker = parser.add_mutually_exclusive_group()
    picker.add_argument("--finder-selection", action="store_true")
    picker.add_argument("--choose", action="store_true")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--reveal", action="store_true")
    parser.add_argument("--plan", action="store_true", help="inspect conversion without creating output")
    args = parser.parse_args(argv)
    try:
        configure_tools()
        run(args)
    except (media.Error, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        if args.notify: notify(str(error)[:180])
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
