#!/usr/bin/env python3
"""Estimate rolling tempo across an audio/video file and export an offline report."""

import argparse
import csv
import html
import json
import math
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
SCRIPT_DIR = Path(__file__).resolve().parent
APP = next((p for p in SCRIPT_DIR.parents if p.suffix == ".app"), None)
BUILD = APP.parent if APP else SCRIPT_DIR.parent / "build"
CACHE = BUILD / "bpm-cache"
CACHE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("NUMBA_CACHE_DIR", str(CACHE / "numba"))
os.environ.setdefault("MPLCONFIGDIR", str(CACHE / "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(CACHE))

import performance_audio as media
from convert_video import configure_tools


SAMPLE_RATE = 22050
HOP = 256


def libraries():
    try:
        import numpy as np
        import librosa
        from scipy import signal
    except ImportError as error:
        raise media.Error("BPM dependencies are missing. Run /opt/homebrew/bin/python3 "
                          "~/dev/scripts/macbook/scripts/setup_bpm_analysis.py, then use "
                          "macbook/build/bpm-venv/bin/python.") from error
    return np, librosa, signal


def tempo_evidence(envelope, frame_rate, grid):
    """Score beat periods and their next three repetitions, not just one peak.

    Multiple-period evidence remains useful when alternating strums make every
    second/third attack stronger than the intervening musical beats.
    """
    np, _, signal = libraries()
    scores = np.zeros(len(grid))
    if len(envelope) < frame_rate * 2 or np.percentile(envelope, 95) < 0.05:
        return scores, 0, False
    peaks, _ = signal.find_peaks(envelope, distance=max(1, int(frame_rate * 60 / grid[-1] / 2)),
                                 prominence=max(0.05, float(np.percentile(envelope, 95)) * 0.2))
    if len(peaks) < 4:
        return scores, len(peaks), False
    x = (envelope - np.mean(envelope)) * np.hanning(len(envelope))
    energy = np.concatenate(([0.0], np.cumsum(x * x)))
    if energy[-1] < 1e-10:
        return scores, len(peaks), False
    correlation = signal.correlate(x, x, mode="full", method="fft")[len(x)-1:]
    lags = np.arange(len(correlation))
    denominator = np.sqrt(np.maximum(0, energy[len(x)-lags] * (energy[-1] - energy[lags])))
    correlation = np.clip(correlation / np.maximum(denominator, 1e-12), -1, 1)
    weights = np.zeros(len(grid))
    for multiple, weight in ((1, 1), (2, 0.85), (3, 0.7), (4, 0.55)):
        lag = 60 * frame_rate * multiple / grid
        supported = lag <= len(x) / 2  # Never score from only a tiny overlap.
        scores += np.where(supported, weight * np.interp(lag, lags, correlation), 0)
        weights += weight * supported
    scores = np.maximum(0, scores / np.maximum(weights, 1e-12))
    return scores, len(peaks), bool(np.max(scores) >= 0.10)


def track_tempo(evidence, usable, times, grid, starting_bpm=None, smoothness=180):
    """Viterbi trajectory on a dense log-tempo grid, regularized per elapsed second.

    Optional guidance only anchors the first supported window (within about
    +/-25%). It imposes neither a later tempo nor an acceleration timestamp.
    Unusable windows have no emission evidence and are omitted from the report.
    """
    np, _, _ = libraries()
    valid = np.flatnonzero(usable)
    if not len(valid):
        return [None] * len(times)
    first = int(valid[0])
    log_grid = np.log2(grid)
    jump = (log_grid[:, None] - log_grid[None, :]) ** 2
    costs, back = [], []
    for index in range(first, len(times)):
        local = -np.log(np.maximum(evidence[index], 1e-4)) if usable[index] else np.zeros(len(grid))
        if index == first:
            if starting_bpm is None:
                local += 0.5 * (np.log2(grid / 120) / 0.8) ** 2
            else:
                distance = np.log2(grid / starting_bpm)
                local += 0.5 * (distance / 0.2) ** 2
                local[np.abs(distance) > 0.32] = 1e9
            costs.append(local); back.append(None)
            continue
        elapsed = max(0.01, times[index] - times[index-1])
        totals = costs[-1][None, :] + jump * smoothness / elapsed
        choices = np.argmin(totals, axis=1)
        costs.append(local + totals[np.arange(len(grid)), choices]); back.append(choices)
    chosen = int(np.argmin(costs[-1]))
    path = [None] * len(times)
    for offset in range(len(costs)-1, -1, -1):
        path[first + offset] = chosen
        if offset:
            chosen = int(back[offset][chosen])
    return path


def analyze_samples(samples, sample_rate=SAMPLE_RATE, points=60, window_seconds=None,
                    min_bpm=45, max_bpm=220, bpm_hint=None):
    np, librosa, signal = libraries()
    duration = len(samples) / sample_rate
    if duration <= 0 or not np.isfinite(samples).all():
        raise media.Error("Audio must contain finite samples and have positive duration.")
    window = min(duration, window_seconds if window_seconds is not None else max(8, 2 * duration / points))
    onset = librosa.onset.onset_strength(y=samples, sr=sample_rate, hop_length=HOP,
                                       n_fft=2048, n_mels=80, fmin=40,
                                       fmax=min(8000, sample_rate/2), max_size=3)
    frame_rate = sample_rate / HOP
    grid = np.exp2(np.arange(np.log2(min_bpm), np.log2(max_bpm) + 1e-9, 1/240))
    times = (np.arange(points) + 0.5) * duration / points
    data, evidence, usable = [], [], []
    for center in times:
        start = min(max(0, float(center) - window / 2), max(0, duration - window))
        end = min(duration, start + window)
        segment = samples[int(start * sample_rate):int(end * sample_rate)]
        rms = float(np.sqrt(np.mean(segment.astype(np.float64) ** 2)))
        rms_db = 20 * math.log10(rms) if rms > 0 else None
        local = onset[int(math.ceil(start * frame_rate)):int(math.floor(end * frame_rate))]
        scores, onsets, supported = tempo_evidence(local, frame_rate, grid)
        supported = supported and rms_db is not None and rms_db > -65
        evidence.append(scores); usable.append(supported)
        peaks, _ = signal.find_peaks(scores)
        peaks = sorted(peaks, key=lambda i: scores[i], reverse=True)[:8]
        candidates = [{"bpm": round(float(grid[i]), 3), "clarity": round(float(scores[i]), 4),
                       "score": float(scores[i])} for i in peaks if scores[i] >= 0.10]
        winner = int(np.argmax(scores))
        data.append({"time_s": float(center), "window_start_s": start, "window_end_s": end,
                     "rms_dbfs": rms_db, "bpm": None, "clarity": 0.0, "ambiguous": False,
                     "candidates": candidates, "onsets": onsets,
                     "local_bpm": round(float(grid[winner]), 3) if supported else None,
                     "continuity_selected": False})
    path = track_tempo(evidence, usable, times, grid, bpm_hint)
    for index, chosen in enumerate(path):
        if chosen is None or not usable[index] or evidence[index][chosen] < 0.12:
            continue  # Do not display an unsupported state carried across a gap.
        point = data[index]
        value, clarity = float(grid[chosen]), float(evidence[index][chosen])
        competitors = [c for c in point["candidates"] if c["score"] >= clarity * 0.9 and
                       abs(math.log2(c["bpm"] / value)) > 0.15]
        changed = abs(value - point["local_bpm"]) > 0.05
        point.update(bpm=round(value, 3), clarity=round(clarity, 4),
                     continuity_selected=changed, ambiguous=bool(competitors))
    valid = [point["bpm"] for point in data if point["bpm"] is not None]
    return {"version": 2, "duration_s": duration, "point_count": points, "window_seconds": window,
            "min_bpm": min_bpm, "max_bpm": max_bpm, "bpm_hint": bpm_hint,
            "median_bpm": float(np.median(valid)) if valid else None,
            "valid_points": len(valid), "points": data,
            "analysis_parameters": {"hop_length": HOP, "n_fft": 2048, "mel_bands": 80,
                                    "onset_max_size": 3, "rms_gate_dbfs": -65,
                                    "minimum_onsets": 4, "minimum_clarity": 0.12,
                                    "period_multiples": [1, 2, 3, 4], "period_weights": [1, 0.85, 0.7, 0.55],
                                    "tempo_bins_per_octave": 240, "trajectory_smoothness": 180},
            "method": "Multi-period onset autocorrelation; dense tempo-grid trajectory regularized per elapsed second",
            "clarity_note": "Heuristic periodicity score, not a probability; metrical ambiguity remains."}


def decode(source, directory, audio_track):
    np, _, _ = libraries()
    info = media.probe(source)
    audio = media.stream(info, "audio", audio_track)
    origin = float(info.get("format", {}).get("start_time", audio.get("start_time", 0)) or 0)
    offset = float(audio.get("start_time", origin)) - origin
    length = offset + media.duration(info, audio)
    videos = [s for s in info["streams"] if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")]
    if videos:
        length = max(length, float(videos[0].get("start_time", origin)) - origin + media.duration(info, videos[0]))
    filters = ["asetpts=PTS-STARTPTS"]
    if offset > 0:
        filters.append(f"adelay={offset * 1000:.6f}:all=1")
    elif offset < 0:
        filters += [f"atrim=start={-offset:.9f}", "asetpts=PTS-STARTPTS"]
    filters += ["apad", f"atrim=duration={length:.9f}"]
    raw = directory / "analysis.f32"
    media.run([media.tool("ffmpeg"), "-v", "error", "-nostdin", "-i", source,
               "-map", f"0:{audio['index']}", "-af", ",".join(filters), "-ac", "1",
               "-ar", str(SAMPLE_RATE), "-c:a", "pcm_f32le", "-f", "f32le", raw])
    if raw.stat().st_size == 0:
        raise media.Error("The selected audio track decoded to no samples.")
    return np.fromfile(raw, dtype="<f4"), audio


def plot_report(report, folder):
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["svg.fonttype"] = "none"
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    np, _, _ = libraries()
    points = report["points"]
    fig, ax = plt.subplots(figsize=(11, 5.2))
    fig.subplots_adjust(left=0.09, right=0.98, top=0.90, bottom=0.18)
    fig.set_facecolor("#ffffff"); ax.set_facecolor("#f8fafc")
    times = [p["time_s"] for p in points]
    bpms = [p["bpm"] if p["bpm"] is not None else np.nan for p in points]
    ax.plot(times, bpms, color="#94a3b8", linewidth=1.2, zorder=1)
    for index, point in enumerate(points):
        if point["bpm"] is None:
            continue
        uncertain = point["ambiguous"] or point["clarity"] < 0.4
        dot = ax.scatter(point["time_s"], point["bpm"], s=36,
                         color="#c1780a" if uncertain else "#2563eb", zorder=2)
        dot.set_gid(f"tempo-point-{index}")
    valid = [p["bpm"] for p in points if p["bpm"] is not None]
    if valid:
        padding = max(5, (max(valid)-min(valid)) * 0.15)
        ax.set_ylim(max(0, min(valid)-padding), max(valid)+padding)
    else:
        ax.set_ylim(report["min_bpm"], report["max_bpm"])
        ax.text(0.5, 0.5, "No reliable rhythmic estimates", ha="center", va="center", transform=ax.transAxes)
    ax.set_xlim(0, report["duration_s"])
    ax.xaxis.set_major_formatter(FuncFormatter(lambda t, _: f"{int(t)//60}:{int(t)%60:02d}"))
    ax.set(xlabel="Time (minutes:seconds)", ylabel="Tempo (BPM)", title="BPM over time")
    ax.grid(alpha=0.18)
    ax.spines[["top", "right"]].set_visible(False)
    fig.text(0.5, 0.025, f"{report['point_count']} samples · {report['window_seconds']:.1f}s rolling window · amber = weak or competing tempo candidates",
             ha="center", fontsize=9, color="#475569")
    fig.savefig(folder / "bpm-over-time.png", dpi=160)
    svg_path = folder / "bpm-over-time.svg"
    fig.savefig(svg_path)
    plt.close(fig)
    svg = svg_path.read_text()
    return svg[svg.index("<svg"):]


def html_report(report, svg):
    title = html.escape(Path(report["source"]).name)
    payload = json.dumps(report, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = Path(__file__).with_name("bpm_report_template.html").read_text()
    parts = {"TITLE": title, "SVG": svg, "DATA": payload}
    return re.sub(r"\{\{(TITLE|SVG|DATA)\}\}", lambda m: parts[m[1]], template)


def save_report(report, parent, stem):
    number = 1
    while True:
        folder = parent / (stem + "-bpm" + ("" if number == 1 else f"-{number}"))
        try:
            folder.mkdir()
            break
        except FileExistsError:
            number += 1
    try:
        (folder / "analysis.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        with (folder / "bpm-data.csv").open("w", newline="") as file:
            fields = ["time_s", "window_start_s", "window_end_s", "bpm", "local_bpm", "clarity", "ambiguous", "continuity_selected", "onsets", "rms_dbfs"]
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            for point in report["points"]:
                writer.writerow({k: point[k] for k in fields})
        svg = plot_report(report, folder)
        (folder / "bpm-over-time.html").write_text(html_report(report, svg))
    except BaseException:
        print(f"Report generation incomplete; partial files retained at {folder}", file=sys.stderr)
        raise
    return folder / "bpm-over-time.html"


def analyze_file(source, output_dir=None, points=60, window_seconds=None, min_bpm=45, max_bpm=220, bpm_hint=None, audio_track=0):
    source = source.expanduser().resolve()
    before = media.fingerprint(source)
    parent = output_dir.expanduser().resolve() if output_dir else source.parent
    if not parent.is_dir(): raise media.Error(f"Output directory does not exist: {parent}")
    print(f"Decoding audio: {source.name}", flush=True)
    with tempfile.TemporaryDirectory(prefix="bpm-analysis-") as temporary:
        samples, audio = decode(source, Path(temporary), audio_track)
        print(f"Analyzing {len(samples)/SAMPLE_RATE:.1f} seconds into {points} rolling estimates...", flush=True)
        report = analyze_samples(samples, SAMPLE_RATE, points, window_seconds, min_bpm, max_bpm, bpm_hint)
    if media.fingerprint(source) != before:
        raise media.Error("Source changed during analysis; report not saved.")
    report.update(source=str(source), audio_track=audio_track, audio_codec=audio.get("codec_name"),
                  analysis_sample_rate=SAMPLE_RATE)
    result = save_report(report, parent, source.stem)
    print(f"Estimated {report['valid_points']}/{points} points; median {report['median_bpm']} BPM.\nReport: {result}", flush=True)
    return result


def choose_files(finder=False):
    script = '''ObjC.import('Foundation');const app=Application.currentApplication();app.includeStandardAdditions=true;
let files=[];if(USE_FINDER){const f=Application('com.apple.finder');if(f.running())files=f.selection().map(x=>ObjC.unwrap($.NSURL.URLWithString(x.url()).path));
files=files.filter(x=>/\\.(mov|mp4|m4v|mkv|webm|avi|wav|aiff?|m4a|mp3|flac|ogg|opus|aac|caf)$/i.test(x));}
if(!files.length){try{files=app.chooseFile({withPrompt:'Choose audio or video files for BPM analysis',multipleSelectionsAllowed:true}).map(x=>x.toString());}
catch(e){if(e.errorNumber===-128)files=[];else throw e;}}JSON.stringify(files);'''.replace("USE_FINDER", "true" if finder else "false")
    return [Path(p) for p in json.loads(media.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", script]))]


def notify(message):
    subprocess.run(["/usr/bin/osascript", "-e", 'on run argv\ndisplay notification (item 1 of argv) with title "BPM Over Time"\nend run', message], capture_output=True)


def run(args):
    if args.background:
        logs = BUILD / "bpm-analysis-jobs"; logs.mkdir(parents=True, exist_ok=True)
        fd, logfile = tempfile.mkstemp(prefix="job-", suffix=".log", dir=logs)
        command = [sys.executable, str(Path(__file__).resolve()), "--points", str(args.points),
                   "--min-bpm", str(args.min_bpm), "--max-bpm", str(args.max_bpm),
                   "--audio-track", str(args.audio_track)]
        if args.bpm_hint is not None: command += ["--bpm-hint", str(args.bpm_hint)]
        if args.window_seconds is not None: command += ["--window-seconds", str(args.window_seconds)]
        if args.output_dir: command += ["--output-dir", str(args.output_dir.expanduser().resolve())]
        for flag in ("finder_selection", "choose", "open", "notify"):
            if getattr(args, flag): command.append("--" + flag.replace("_", "-"))
        command += [str(p.expanduser().resolve()) for p in args.files]
        with os.fdopen(fd, "w") as log:
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        print(f"Started. Log: {logfile}")
        return
    files = args.files or (choose_files(args.finder_selection) if args.finder_selection or args.choose else [])
    if not files:
        if args.finder_selection or args.choose: return
        raise media.Error("Provide files or use --choose / --finder-selection.")
    errors, done = [], []
    for source in files:
        try:
            report = analyze_file(source, args.output_dir, args.points, args.window_seconds,
                                  args.min_bpm, args.max_bpm, args.bpm_hint, args.audio_track)
            done.append(report)
            if args.open:
                try: media.run(["/usr/bin/open", report])
                except media.Error as error: print(f"Report saved, but could not open it: {error}", file=sys.stderr)
        except (media.Error, OSError, ValueError) as error:
            errors.append(f"{source.name}: {error}"); print(errors[-1], file=sys.stderr, flush=True)
    if errors: raise media.Error(f"{len(done)} reports created; {len(errors)} failed.\n" + "\n".join(errors))
    if args.notify: notify(f"Created {len(done)} BPM report(s).")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", type=Path, nargs="*")
    parser.add_argument("--points", type=int, default=60)
    parser.add_argument("--window-seconds", type=float, help="default max(8, 2*duration/points), limited to file length")
    parser.add_argument("--min-bpm", type=float, default=45)
    parser.add_argument("--max-bpm", type=float, default=220)
    parser.add_argument("--bpm-hint", type=float, help="optional approximate starting tempo; later tempo is free to change")
    parser.add_argument("--audio-track", type=int, default=0)
    parser.add_argument("--output-dir", type=Path)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--finder-selection", action="store_true")
    selection.add_argument("--choose", action="store_true")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--open", action="store_true", help="open the offline HTML graph")
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args(argv)
    if not 2 <= args.points <= 1000: parser.error("--points must be between 2 and 1000")
    if not all(math.isfinite(v) for v in (args.min_bpm, args.max_bpm)) or not 30 <= args.min_bpm < args.max_bpm <= 300:
        parser.error("BPM bounds must be finite and between 30 and 300, with min < max")
    if args.bpm_hint is not None and (not math.isfinite(args.bpm_hint) or not args.min_bpm <= args.bpm_hint <= args.max_bpm):
        parser.error("--bpm-hint must be finite and inside the BPM search range")
    if args.window_seconds is not None and (not math.isfinite(args.window_seconds) or args.window_seconds < 2):
        parser.error("--window-seconds must be finite and at least 2")
    try:
        configure_tools(); run(args)
    except (media.Error, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        if args.notify: notify(str(error)[:180])
        return 1
    except KeyboardInterrupt: return 130
    return 0


if __name__ == "__main__": sys.exit(main())
