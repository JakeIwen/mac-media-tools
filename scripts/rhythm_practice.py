#!/usr/bin/env python3
"""Create an offline, editable acoustic rhythm practice report."""
import argparse
import html
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
import bpm_over_time as bpm


def analyze(samples, reference_bpm=None, minimum_gap=.09):
    np, librosa, _ = bpm.libraries()
    rate = bpm.SAMPLE_RATE
    hop = 128
    envelope = librosa.onset.onset_strength(y=samples, sr=rate, hop_length=hop,
        n_fft=1024, n_mels=80, fmin=60, fmax=8000, max_size=3)
    frames = librosa.onset.onset_detect(onset_envelope=envelope, sr=rate,
        hop_length=hop, units="frames", backtrack=False,
        wait=max(1, round(minimum_gap * rate / hop)))
    scale = max(float(envelope.max()), 1e-12)
    events = [{"id": int(i), "time": round(float(frame * hop / rate), 6),
               "strength": round(float(envelope[frame] / scale), 4), "enabled": True}
              for i, frame in enumerate(frames)]
    tempo = bpm.analyze_samples(samples, window_seconds=16)
    suggested = reference_bpm or tempo["median_bpm"] or 120
    # The phase is only an initial subdivision alignment, not an inferred downbeat.
    step = 60 / suggested / 2
    vector = sum(e["strength"] * np.exp(2j * np.pi * e["time"] / step) for e in events)
    phase = float((np.angle(vector) % (2*np.pi)) / (2*np.pi) * step) if events else 0
    size = max(1, math.ceil(len(samples) / 3600))
    waveform = [round(float(np.max(np.abs(samples[i:i+size]))), 5)
                for i in range(0, len(samples), size)]
    return {"version": 1, "duration": len(samples)/rate, "events": events,
            "waveform": waveform, "waveform_step": size/rate,
            "reference": {"bpm": round(suggested, 3), "offset": round(phase, 6),
                          "subdivisions": 2, "beats": 4},
            "tempo_estimates": tempo["points"],
            "reference_supplied": reference_bpm is not None,
            "detection": {"hop_seconds": hop/rate, "minimum_gap_seconds": minimum_gap,
                          "method": "Spectral-flux peaks; attack strength is not calibrated loudness"}}


def render(report):
    root = Path(__file__).parent
    payload = json.dumps(report, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    parts = {"TITLE": html.escape(Path(report["source"]).name), "DATA": payload,
             "CORE": (root / "rhythm_core.js").read_text(),
             "UI": (root / "rhythm_ui.js").read_text()}
    return re.sub(r"\{\{(TITLE|DATA|CORE|UI)\}\}", lambda m: parts[m[1]],
                  (root / "rhythm_report_template.html").read_text())


def analyze_file(source, output_dir=None, reference_bpm=None, minimum_gap=.09, audio_track=0):
    source = source.expanduser().resolve()
    before = bpm.media.fingerprint(source)
    parent = output_dir.expanduser().resolve() if output_dir else source.parent
    if not parent.is_dir(): raise bpm.media.Error("Output directory does not exist.")
    print(f"Analyzing attacks: {source.name}", flush=True)
    with tempfile.TemporaryDirectory(prefix="rhythm-analysis-") as scratch:
        samples, audio = bpm.decode(source, Path(scratch), audio_track)
        report = analyze(samples, reference_bpm, minimum_gap)
    report.update(source=str(source), audio_track=audio_track, audio_codec=audio.get("codec_name"))
    if bpm.media.fingerprint(source) != before: raise bpm.media.Error("Source changed during analysis.")
    number = 1
    while True:
        folder = parent / (source.stem + "-rhythm" + (f"-{number}" if number > 1 else ""))
        try: folder.mkdir(); break
        except FileExistsError: number += 1
    try:
        bpm.export_playback(source, folder / "playback.m4a", audio_track)
        if bpm.media.fingerprint(source) != before: raise bpm.media.Error("Source changed during analysis.")
        (folder / "analysis.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
        page = folder / "rhythm-practice.html"
        page.write_text(render(report))
    except BaseException:
        print(f"Incomplete report retained: {folder}", file=sys.stderr)
        raise
    print(f"Detected {len(report['events'])} candidate attacks. Report: {page}", flush=True)
    return page


def notify(message):
    subprocess.run(["/usr/bin/osascript", "-e", 'on run argv\ndisplay notification (item 1 of argv) with title "Rhythm Practice"\nend run', message], capture_output=True)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("files", nargs="*", type=Path)
    p.add_argument("--output-dir", type=Path)
    p.add_argument("--bpm", type=float, help="initial fixed reference tempo; editable in report")
    p.add_argument("--minimum-gap", type=float, default=.09, help="minimum gap between detected attacks, seconds")
    p.add_argument("--audio-track", type=int, default=0)
    group = p.add_mutually_exclusive_group()
    group.add_argument("--finder-selection", action="store_true")
    group.add_argument("--choose", action="store_true")
    for flag in ("background", "open", "notify"): p.add_argument("--"+flag, action="store_true")
    args = p.parse_args(argv)
    if args.bpm is not None and (not math.isfinite(args.bpm) or not 30 <= args.bpm <= 300): p.error("--bpm must be 30–300")
    if not math.isfinite(args.minimum_gap) or not .03 <= args.minimum_gap <= .5: p.error("--minimum-gap must be .03–.5 seconds")
    if args.audio_track < 0: p.error("--audio-track must be nonnegative")
    try:
        bpm.configure_tools()
        if args.background:
            logs = bpm.BUILD / "rhythm-analysis-jobs"; logs.mkdir(parents=True, exist_ok=True)
            command = [sys.executable, str(Path(__file__).resolve()), "--minimum-gap", str(args.minimum_gap), "--audio-track", str(args.audio_track)]
            if args.bpm is not None: command += ["--bpm", str(args.bpm)]
            if args.output_dir: command += ["--output-dir", str(args.output_dir.expanduser().resolve())]
            for flag in ("finder_selection", "choose", "open", "notify"):
                if getattr(args, flag): command.append("--"+flag.replace("_", "-"))
            command += [str(f.expanduser().resolve()) for f in args.files]
            with tempfile.NamedTemporaryFile(prefix="job-", suffix=".log", dir=logs, delete=False) as log:
                subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
                print(f"Started. Log: {log.name}")
            return 0
        files = args.files or (bpm.choose_files(args.finder_selection) if args.finder_selection or args.choose else [])
        if not files:
            if args.finder_selection or args.choose: return 0
            raise bpm.media.Error("Provide files or use --choose.")
        done, errors = 0, []
        for file in files:
            try:
                page = analyze_file(file, args.output_dir, args.bpm, args.minimum_gap, args.audio_track)
                done += 1
                if args.open: bpm.media.run(["/usr/bin/open", page])
            except (bpm.media.Error, OSError, ValueError) as error: errors.append(f"{file.name}: {error}")
        if errors: raise bpm.media.Error(f"{done} reports created; failures: " + "; ".join(errors))
        if args.notify: notify(f"Created {done} rhythm report(s).")
    except (bpm.media.Error, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        if args.notify: notify(str(error)[:180])
        return 1
    return 0


if __name__ == "__main__": sys.exit(main())
