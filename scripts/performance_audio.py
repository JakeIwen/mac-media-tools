#!/usr/bin/env python3
"""Prepare iPhone performances for GarageBand and replace their soundtracks."""

import argparse
import ctypes
import json
import math
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

# Imported effects must not create __pycache__ inside a signed app bundle.
sys.dont_write_bytecode = True


MANIFEST = "performance.json"
AUDIO_EXTENSIONS = {".wav", ".aif", ".aiff", ".m4a", ".caf", ".flac"}


class Error(Exception):
    pass


def tool(name):
    override = os.environ.get(name.upper())
    candidates = [override] if override else [
        str(Path(__file__).resolve().parent / "bin" / name),
        shutil.which(name), f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise Error(f"Cannot find {name}. In Terminal, run: brew install ffmpeg\n"
                f"Or set {name.upper()} to its executable path.")


def run(args):
    result = subprocess.run([str(a) for a in args], capture_output=True, text=True)
    if result.returncode:
        raise Error(result.stderr.strip() or f"Command failed: {args[0]}")
    return result.stdout


def probe(path):
    if not path.is_file():
        raise Error(f"File not found: {path}")
    return json.loads(run([tool("ffprobe"), "-v", "error", "-show_streams",
                           "-show_format", "-of", "json", path]))


def stream(info, kind, index=0):
    streams = [s for s in info["streams"] if s["codec_type"] == kind
               and not s.get("disposition", {}).get("attached_pic")]
    if not 0 <= index < len(streams):
        raise Error(f"No {kind} track {index}; found {len(streams)}.")
    return streams[index]


def duration(info, track):
    value = track.get("duration", info.get("format", {}).get("duration"))
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise Error("Cannot determine media duration; no output was published.")
    if not math.isfinite(seconds) or seconds <= 0:
        raise Error("Media duration must be finite and positive.")
    return seconds


def fingerprint(path):
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def rotation(track):
    return next((s["rotation"] for s in track.get("side_data_list", [])
                 if "rotation" in s), 0)


def validate_video(original, result, audio_count):
    old, new = stream(original, "video"), stream(result, "video")
    for key in ("codec_name", "width", "height", "pix_fmt", "color_range",
                "color_space", "color_transfer", "color_primaries"):
        if old.get(key) != new.get(key):
            raise Error(f"Output changed video {key}; no output was published.")
    if rotation(old) != rotation(new):
        raise Error("Output changed video rotation; no output was published.")
    for side in old.get("side_data_list", []):
        if side.get("side_data_type") in {"DOVI configuration record",
                                            "Mastering display metadata",
                                            "Content light level metadata"}:
            if side not in new.get("side_data_list", []):
                raise Error("Output lost HDR metadata; no output was published.")
    if abs(duration(original, old) - duration(result, new)) > 0.05:
        raise Error("Output changed video duration; no output was published.")
    count = sum(s["codec_type"] == "audio" for s in result["streams"])
    if count != audio_count:
        raise Error(f"Expected {audio_count} output audio tracks, found {count}.")


def move_no_replace(source, destination):
    """Publish atomically without hard links on macOS or overwriting an existing file."""
    if sys.platform == "darwin":
        # RENAME_EXCL from the macOS SDK's stdio.h. Unlike os.rename(), this
        # rejects an existing destination atomically, including a dangling symlink.
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(os.fsencode(source), os.fsencode(destination), 0x00000004):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(source), None, str(destination))
    else:
        os.link(source, destination)
        source.unlink()


def publish(args, destination, validate=None, numbered=False, preserve_completed=False):
    """Write privately, validate, then publish with atomic no-clobber semantics."""
    destination = destination.absolute()
    if not destination.parent.is_dir():
        raise Error(f"Output directory does not exist: {destination.parent}")
    if not numbered and os.path.lexists(destination):
        raise Error(f"Refusing to overwrite: {destination}")
    fd, temporary = tempfile.mkstemp(prefix=".performance-", suffix=destination.suffix,
                                     dir=destination.parent)
    os.close(fd)
    temp = Path(temporary)
    keep_temp = False
    try:
        run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin",
             "-y", *args, temp])
        if validate:
            validate(probe(temp))
        candidate, number = destination, 2
        while True:
            try:
                move_no_replace(temp, candidate)
                break
            except FileExistsError:
                if not numbered:
                    raise Error(f"Refusing to overwrite: {candidate}")
                candidate = destination.with_name(
                    f"{destination.stem}-{number}{destination.suffix}")
                number += 1
            except OSError as error:
                if preserve_completed:
                    keep_temp = True
                    raise Error(f"Processing completed, but saving as {candidate} failed: {error}.\n"
                                f"The validated video is retained at: {temp}") from error
                raise
        return candidate
    finally:
        if not keep_temp:
            temp.unlink(missing_ok=True)


def mov_options():
    return ["-map_metadata", "0", "-map_chapters", "0", "-movflags",
            "+faststart+use_metadata_tags"]


def timed_input(path, track):
    # With -copyts, align the selected track's first presentation time to zero,
    # even when another track determines an earlier container start timestamp.
    start = float(track.get("start_time", 0))
    if not math.isfinite(start):
        raise Error("Invalid track start time.")
    return ["-itsoffset", f"{-start:.9f}", "-i", path]


def strip(video, output=None):
    info = probe(video)
    track = stream(info, "video")
    destination = output or video.with_name(video.stem + "-silent.mov")
    if destination.suffix.lower() != ".mov":
        raise Error("Use a .mov output for a silent copy.")
    return publish(["-copyts", *timed_input(video, track), "-map", f"0:{track['index']}", "-c:v", "copy",
                    "-an", *mov_options()], destination,
                   lambda result: validate_video(info, result, 0), output is None,
                   preserve_completed=True)


def lossless_codec(audio):
    codec = audio.get("codec_name", "")
    # Preserve integer/float PCM and Apple Lossless without another conversion.
    if codec.startswith("pcm_") or codec == "alac":
        return "copy"
    bits = int(audio.get("bits_per_raw_sample", 0) or 0)
    return "pcm_s32le" if bits > 24 else "pcm_s24le"


def replace(video, audio, output=None, share=False, tolerance=0.25,
            allow_mismatch=False, numbered=False, audio_filter=None):
    before_video, before_audio = fingerprint(video), fingerprint(audio)
    info, audio_info = probe(video), probe(audio)
    v, a = stream(info, "video"), stream(audio_info, "audio")
    vd, ad = duration(info, v), duration(audio_info, a)
    delta = ad - vd
    if abs(delta) > tolerance:
        message = (f"Duration mismatch: video {vd:.3f}s, edited audio {ad:.3f}s "
                   f"({delta:+.3f}s). Export the full performance from time zero.")
        if not allow_mismatch:
            raise Error(message + "\nUse --allow-duration-mismatch only if intentional; "
                        "it keeps both tracks in full without stretching or trimming.")
        print("Warning: " + message, file=sys.stderr)
    suffix = "-share.mp4" if share else "-finished.mov"
    destination = output or video.with_name(video.stem + suffix)
    if destination.suffix.lower() != (".mp4" if share else ".mov"):
        raise Error("Use .mp4 with --share, or .mov for lossless output.")
    encoding = ["-c:a", "aac", "-b:a", "320k"] if share else [
        "-c:a", "pcm_s24le" if audio_filter else lossless_codec(a)]
    if audio_filter:
        encoding += ["-af", audio_filter]

    def validate(result):
        validate_video(info, result, 1)
        actual = stream(result, "audio")
        if abs(duration(result, actual) - ad) > 0.1:
            raise Error("Output changed audio duration; no output was published.")
        if fingerprint(video) != before_video or fingerprint(audio) != before_audio:
            raise Error("An input changed during processing. Try again after export finishes.")

    # Each selected track is rebased to zero. No -shortest, audio mixing,
    # video encoding, frame-rate override, sample-rate override, or time stretching.
    return publish(["-copyts", *timed_input(video, v), *timed_input(audio, a),
                    "-map", f"0:{v['index']}",
                    "-map", f"1:{a['index']}", "-c:v", "copy", *encoding,
                    *mov_options()], destination, validate, numbered or output is None,
                   preserve_completed=True)


def write_launchers(project):
    command = shlex.join([sys.executable, str(Path(__file__).resolve())])
    for name, subcommand in (("Finish", "finish"), ("Auto Finish", "watch"),
                             ("Make Sharing Copy", "finish --share")):
        launcher = project / f"{name}.command"
        launcher.write_text(
            '#!/bin/zsh\ncd -- "${0:A:h}" || exit 1\n'
            f'{command} {subcommand} .\nresult=$?\n'
            'printf "\\nPress Return to close."\nread -r reply\nexit "$result"\n')
        launcher.chmod(0o755)


def prepare(video, root=None, audio_track=0, silent=False):
    video = video.resolve()
    before = fingerprint(video)
    info = probe(video)
    v, a = stream(info, "video"), stream(info, "audio", audio_track)
    vd = duration(info, v)
    parent = root.resolve() if root else video.parent
    parent.mkdir(parents=True, exist_ok=True)
    project = parent / f"{video.name}.performance"
    project.mkdir()  # Existing projects are never silently reused or overwritten.
    try:
        # Preserve the source audio's offset relative to the first video frame.
        offset = float(a.get("start_time", 0)) - float(v.get("start_time", 0))
        filters = ["asetpts=PTS-STARTPTS"]
        if offset > 0:
            samples = round(offset * int(a["sample_rate"]))
            filters.append(f"adelay={samples}S:all=1")
        elif offset < 0:
            filters += [f"atrim=start={-offset:.9f}", "asetpts=PTS-STARTPTS"]
        filters += ["apad", f"atrim=duration={vd:.9f}"]
        print(f"Extracting: {video.name}", flush=True)
        publish(["-i", video, "-map", f"0:{a['index']}", "-af", ",".join(filters),
                 "-c:a", "pcm_s24le", "-rf64", "auto", "-map_metadata", "-1"],
                project / "original.wav")
        if fingerprint(video) != before:
            raise Error("Source video changed during extraction. Prepare it again.")
        manifest = {"version": 1, "video": str(video),
                    "video_relative": os.path.relpath(video, project),
                    "fingerprint": before, "audio_track": audio_track,
                    "video_duration": vd}
        (project / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n")
        write_launchers(project)
        (project / "READ ME.txt").write_text(
            f"PERFORMANCE AUDIO\n\nSource: {video.name}\nDuration: {vd:.3f} seconds\n\n"
            "1. Import original.wav into GarageBand at the very start (bar 1).\n"
            "2. Add effects; keep the timing and full performance length.\n"
            "3. Export WAVE or AIFF, preferably 24-bit, here as edited.wav or edited.aiff.\n"
            "4. Double-click Finish.command to create a lossless MOV.\n\n"
            "Shortcut: start Auto Finish.command before exporting; it waits for edited.*\n"
            "and makes a new numbered MOV each time you export. Ctrl-C stops watching.\n"
            "Make Sharing Copy.command produces MP4 with 320 kb/s AAC audio.\n"
            "The video is copied without re-encoding. Keep the original video in place.\n"
            "Do not leave multiple edited.* formats here: choose one.\n"
            "Only one selected original audio track is extracted; use --audio-track\n"
            "to select another. The finished video contains only the edited audio.\n")
        if silent:
            strip(video, project / "silent.mov")
    except BaseException:
        # Keep a failed project for diagnosis; never recursively delete media.
        print(f"Preparation incomplete; inspect {project} before retrying.", file=sys.stderr)
        raise
    print(f"Ready: {project}", flush=True)
    return project


def load_project(project):
    try:
        manifest = json.loads((project / MANIFEST).read_text())
        if manifest["version"] != 1:
            raise Error("Unsupported project version.")
        candidates = [(project / manifest["video_relative"]).resolve(),
                      Path(manifest["video"])]
        for candidate in candidates:
            if candidate.is_file() and fingerprint(candidate) == manifest["fingerprint"]:
                return candidate
    except (KeyError, ValueError, TypeError) as error:
        raise Error(f"Invalid {MANIFEST}: {error}")
    raise Error("Original video is missing or has changed since preparation. "
                "Keep the source beside its project, or prepare the new source again.")


def find_edited(project):
    matches = sorted(p for p in project.iterdir() if p.is_file()
                     and p.stem.lower() == "edited" and p.suffix.lower() in AUDIO_EXTENSIONS)
    if len(matches) != 1:
        raise Error(f"Expected exactly one edited.wav, edited.aiff, edited.m4a, etc. "
                    f"in {project}; found {len(matches)}. "
                    "Or specify the audio file explicitly with finish --audio FILE.")
    return matches[0]


def finish(project, audio=None, share=False, tolerance=0.25, allow_mismatch=False):
    video = load_project(project)
    audio = audio or find_edited(project)
    destination = project / ("finished-share.mp4" if share else "finished.mov")
    result = replace(video, audio, destination, share, tolerance, allow_mismatch,
                     numbered=True)
    print(f"Created: {result}", flush=True)
    return result


def watch(projects, share=False, settle=5.0, once=False):
    """Only process a stable export once; errors retry after the file changes."""
    projects = [p.resolve() for p in projects]
    for project in projects:
        load_project(project)
    pending, attempted, messages = {}, {}, {}
    print(f"Watching {len(projects)} project(s) for edited.*; Ctrl-C stops.", flush=True)
    while True:
        for project in projects:
            try:
                audio = find_edited(project)
                signature = (str(audio), *fingerprint(audio).values())
            except (Error, OSError) as error:
                pending.pop(project, None)
                if messages.get(project) != str(error):
                    print(f"Waiting: {error}", flush=True)
                    messages[project] = str(error)
                continue
            messages.pop(project, None)
            if signature == attempted.get(project):
                continue
            previous, since = pending.get(project, (None, time.monotonic()))
            if previous != signature:
                pending[project] = (signature, time.monotonic())
                continue
            if time.monotonic() - since < settle:
                continue
            attempted[project] = signature
            try:
                finish(project, audio, share)
                if once:
                    return
            except (Error, OSError) as error:
                print(f"Not finished: {error}\nRe-export the audio to retry.",
                      file=sys.stderr, flush=True)
        time.sleep(1)


def nonnegative(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise argparse.ArgumentTypeError("must be a finite, nonnegative number")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="check FFmpeg and ffprobe")
    presets = commands.add_parser("presets", help="list automatic effects presets")
    presets.add_argument("--config", type=Path)
    effects = commands.add_parser("process", help="apply effects and normalize; no GarageBand needed")
    effects.add_argument("videos", type=Path, nargs="*")
    effects.add_argument("--preset", help="preset name (default from config)")
    effects.add_argument("--config", type=Path, help="custom presets JSON")
    effects.add_argument("--ambience", type=float, help="override short ambience return level, 0–50 percent of dry")
    effects.add_argument("--reverb", type=float, help="override long reverb return level, 0–50 percent of dry")
    effects.add_argument("--audio-track", type=int, default=0)
    effects.add_argument("--share", action="store_true", help="MP4/AAC instead of lossless MOV")
    selection = effects.add_mutually_exclusive_group()
    selection.add_argument("--finder-selection", action="store_true", help="selected Finder videos; picker if none")
    selection.add_argument("--choose", action="store_true", help="show a file picker")
    effects.add_argument("--reveal", action="store_true")
    effects.add_argument("--notify", action="store_true")
    effects.add_argument("--background", action="store_true", help="launch a detached job and print its log path")
    prep = commands.add_parser("prepare", help="extract WAV and create GarageBand work folders")
    prep.add_argument("videos", type=Path, nargs="+")
    prep.add_argument("--out-dir", type=Path, help="parent directory for new projects")
    prep.add_argument("--audio-track", type=int, default=0, help="zero-based audio track (default 0)")
    prep.add_argument("--silent", action="store_true", help="also create a video-only MOV")
    prep.add_argument("--reveal", action="store_true", help="open the prepared folder in Finder")
    fin = commands.add_parser("finish", help="combine a project with its edited audio")
    fin.add_argument("project", type=Path)
    fin.add_argument("--audio", type=Path, help="explicit GarageBand export instead of edited.*")
    rep = commands.add_parser("replace", help="combine any video and edited audio directly")
    rep.add_argument("video", type=Path)
    rep.add_argument("audio", type=Path)
    rep.add_argument("-o", "--output", type=Path)
    for command in (fin, rep):
        command.add_argument("--share", action="store_true", help="MP4 with 320 kb/s AAC instead of lossless MOV")
        command.add_argument("--tolerance", type=nonnegative, default=0.25, help="allowed duration difference in seconds (default 0.25)")
        command.add_argument("--allow-duration-mismatch", action="store_true", help="keep both full tracks despite unequal durations")
    mute = commands.add_parser("strip", help="make an optional video-only MOV")
    mute.add_argument("video", type=Path)
    mute.add_argument("-o", "--output", type=Path)
    watcher = commands.add_parser("watch", help="automatically finish new GarageBand exports")
    watcher.add_argument("projects", type=Path, nargs="+")
    watcher.add_argument("--share", action="store_true")
    watcher.add_argument("--settle", type=nonnegative, default=5, help="seconds the export must stop changing (default 5)")
    watcher.add_argument("--once", action="store_true", help="exit after the first successful finish")
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            for name in ("ffmpeg", "ffprobe"):
                executable = tool(name)
                print(f"{executable}: {run([executable, '-version']).splitlines()[0]}")
        elif args.command in ("process", "presets"):
            import performance_audio_effects as effects_module
            if args.command == "presets":
                data = effects_module.load_presets(args.config)
                for name, preset in data["presets"].items():
                    default = " (default)" if name == data["default"] else ""
                    print(f"{name}: {preset['label']}{default}")
            else:
                effects_module.run_process(args)
        elif args.command == "prepare":
            # Verify dependencies before creating folders.
            tool("ffmpeg")
            tool("ffprobe")
            for video in args.videos:
                project = prepare(video, args.out_dir, args.audio_track, args.silent)
                if args.reveal:
                    run(["/usr/bin/open", project])
        elif args.command == "finish":
            finish(args.project.resolve(), args.audio and args.audio.resolve(),
                   args.share, args.tolerance, args.allow_duration_mismatch)
        elif args.command == "replace":
            print(replace(args.video.resolve(), args.audio.resolve(), args.output,
                          args.share, args.tolerance, args.allow_duration_mismatch))
        elif args.command == "strip":
            print(strip(args.video.resolve(), args.output))
        elif args.command == "watch":
            watch(args.projects, args.share, args.settle, args.once)
    except (Error, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        if getattr(args, "notify", False):
            from performance_audio_effects import notify
            notify(f"Failed: {str(error)[:180]}")
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    # The optional effects module imports this file's helpers and exception type.
    sys.modules["performance_audio"] = sys.modules[__name__]
    sys.exit(main())
