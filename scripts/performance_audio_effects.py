"""Offline effects presets; no GarageBand, plug-ins, network, or pip required."""

import array
import json
import math
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import wave

import performance_audio as pa


DEFAULT_CONFIG = Path(__file__).with_name("performance_audio_presets.json")
RANGES = {
    "target_lufs": (-40, -8), "true_peak_db": (-9, -0.5),
    "max_boost_db": (0, 30), "highpass_hz": (0, 300),
    "compressor_ratio": (1, 4), "compressor_threshold_db": (-40, -6),
    "reverb_mix": (0, 0.5), "reverb_decay_seconds": (0.1, 4),
    "reverb_predelay_ms": (0, 100),
    "input_target_lufs": (-40, -8), "input_true_peak_db": (-12, -0.5),
    "ambience_mix": (0, 0.5), "ambience_decay_seconds": (0.05, 0.8),
    "ambience_predelay_ms": (0, 50),
}
OPTIONAL_DEFAULTS = {"input_target_lufs": -16, "input_true_peak_db": -3,
                     "ambience_mix": 0, "ambience_decay_seconds": 0.25,
                     "ambience_predelay_ms": 6}


def load_presets(config=None):
    config = config or DEFAULT_CONFIG
    data = json.loads(config.read_text())
    if set(data) != {"default", "presets"} or not isinstance(data["presets"], dict):
        raise pa.Error("Preset config needs default and presets fields.")
    if data["default"] not in data["presets"]:
        raise pa.Error("Default preset is missing from presets.")
    for name, preset in data["presets"].items():
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", name):
            raise pa.Error(f"Invalid preset name: {name}")
        if isinstance(preset, dict):
            for key, value in OPTIONAL_DEFAULTS.items():
                preset.setdefault(key, value)  # Keep existing user preset files compatible.
            preset.setdefault("eq_bands", [])
        if not isinstance(preset, dict) or set(preset) != {*RANGES, "label", "eq_bands"}:
            raise pa.Error(f"Preset {name} must contain label and all documented settings.")
        if not isinstance(preset["label"], str) or not preset["label"].strip():
            raise pa.Error(f"Preset {name} needs a label.")
        for key, (low, high) in RANGES.items():
            value = preset[key]
            if (isinstance(value, bool) or not isinstance(value, (float, int))
                    or not math.isfinite(value) or not low <= value <= high):
                raise pa.Error(f"{name}.{key} must be between {low} and {high}.")
        bands = preset["eq_bands"]
        if not isinstance(bands, list) or len(bands) > 8:
            raise pa.Error(f"{name}.eq_bands must be a list of up to 8 bell EQ bands.")
        for band in bands:
            limits = {"frequency_hz": (20, 20000), "gain_db": (-12, 12), "q": (0.1, 10)}
            if not isinstance(band, dict) or set(band) != set(limits):
                raise pa.Error(f"{name}.eq_bands entries need frequency_hz, gain_db, and q.")
            for key, (low, high) in limits.items():
                value = band[key]
                if (isinstance(value, bool) or not isinstance(value, (float, int)) or
                        not math.isfinite(value) or not low <= value <= high):
                    raise pa.Error(f"{name}.eq_bands.{key} must be between {low} and {high}.")
    return data


def eq_filters(preset, sample_rate):
    filters = []
    if preset["highpass_hz"]:
        filters.append(f"highpass=f={preset['highpass_hz']}")
    for band in preset.get("eq_bands", []):
        if band["frequency_hz"] >= sample_rate / 2:
            raise pa.Error("EQ frequency must be below half the audio sample rate.")
        filters.append(f"equalizer=f={band['frequency_hz']}:t=q:w={band['q']}:g={band['gain_db']}")
    return filters


def make_room_ir(path, sample_rate, decay, predelay, early_reflections=False):
    """Deterministic, damped diffuse room tail; no direct sound in the wet IR.

    Energy normalization makes mix settings reasonably comparable between decay
    times. A 5 kHz low-pass softens the late tail. This is synthetic room reverb,
    not a model of GarageBand's plug-in or a measured physical room.
    """
    rng = random.Random(19037 if early_reflections else 74291)
    count = math.ceil(decay * sample_rate)
    alpha = 1 - math.exp(-2 * math.pi * 5000 / sample_rate)
    tail, previous = [], 0.0
    for index in range(count):
        previous += alpha * (rng.uniform(-1, 1) - previous)
        # Fade up briefly for a dense tail without an abrupt noisy onset.
        value = previous * min(1, index / (0.015 * sample_rate))
        tail.append(value * math.exp(-math.log(1000) * index / count))
    norm = math.sqrt(sum(value * value for value in tail))
    if early_reflections:
        # Short discrete reflections plus a quiet diffuse tail, starting at the
        # requested predelay. A separate branch supplies the original dry sound.
        tail = [0.35 * value / norm for value in tail]
        for seconds, gain in ((0, 0.6), (0.007, 0.45), (0.014, -0.30),
                              (0.024, 0.23), (0.039, -0.18), (0.058, 0.12)):
            index = round(seconds * sample_rate)
            if index < count:
                tail[index] += gain
        norm = math.sqrt(sum(value * value for value in tail))
    samples = array.array("h", [0] * round(predelay * sample_rate / 1000))
    samples.extend(round(32767 * value / norm) for value in tail)
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as file:
        file.setparams((1, 2, sample_rate, 0, "NONE", "not compressed"))
        file.writeframes(samples.tobytes())


def spatial_effects(source, folder, preset, rate, length):
    """Mix dry + independent ambience/reverb returns; never feed one into the other.

    Amounts are linear return gains relative to dry=1, not percentages of a fixed
    combined wet/dry budget. Only final loudness normalization scales the mix.
    """
    enabled = [name for name in ("ambience", "reverb") if preset[f"{name}_mix"] > 0]
    if not enabled:
        return source
    inputs = ["-i", source]
    graph = f"[0:a]asplit={len(enabled) + 1}[dry]" + "".join(f"[{name}_send]" for name in enabled)
    for index, name in enumerate(enabled, 1):
        ir = folder / f"{name}-ir.wav"
        make_room_ir(ir, rate, preset[f"{name}_decay_seconds"], preset[f"{name}_predelay_ms"],
                     early_reflections=name == "ambience")
        inputs += ["-i", ir]
        graph += (f";[{name}_send][{index}:a]"
                  f"afir=dry=1:wet=1:irnorm=-1:irfmt=mono:minp=256:maxp=8192[{name}_wet]")
    weights = "1 " + " ".join(str(preset[f"{name}_mix"]) for name in enabled)
    graph += ";[dry]" + "".join(f"[{name}_wet]" for name in enabled)
    graph += (f"amix=inputs={len(enabled) + 1}:weights='{weights}':normalize=0:duration=first,"
              f"apad,atrim=duration={length:.9f},asetpts=PTS-STARTPTS[out]")
    result = folder / "spatial.wav"
    return pa.publish([*inputs, "-filter_complex", graph, "-map", "[out]",
                       "-c:a", "pcm_f32le", "-ar", str(rate), "-rf64", "auto"], result)


def loudness(path):
    result = subprocess.run([
        pa.tool("ffmpeg"), "-hide_banner", "-nostdin", "-i", str(path),
        "-map", "0:a:0", "-af", "loudnorm=I=-16:TP=-1.5:LRA=50:print_format=json",
        "-f", "null", "-"], capture_output=True, text=True)
    if result.returncode:
        raise pa.Error(result.stderr.strip())
    match = re.search(r'\{\s*"input_i".*?\}', result.stderr, re.S)
    if not match:
        raise pa.Error("FFmpeg did not return a loudness measurement.")
    return json.loads(match.group())


def gain_for(stats, preset):
    integrated = float(stats["input_i"])
    peak = float(stats["input_tp"])
    if math.isnan(integrated) or math.isnan(peak) or peak == math.inf:
        raise pa.Error("Invalid loudness measurement.")
    if peak == -math.inf:
        return 0.0  # Silence remains silence.
    ceiling = preset["true_peak_db"] - peak
    if not math.isfinite(integrated):
        # Below the loudness gate (or too short to measure): don't boost noise.
        return min(0, ceiling)
    return min(preset["target_lufs"] - integrated, ceiling, preset["max_boost_db"])


def gain_details(stats, preset, gain):
    integrated, peak = float(stats["input_i"]), float(stats["input_tp"])
    reasons = []
    if peak == -math.inf:
        reasons.append("silence")
    elif not math.isfinite(integrated):
        reasons.append("below loudness gate or too short to measure; no boost")
    else:
        requested = preset["target_lufs"] - integrated
        if gain < requested - 0.01:
            if abs(gain - (preset["true_peak_db"] - peak)) < 0.01:
                reasons.append("true-peak ceiling")
            if abs(gain - preset["max_boost_db"]) < 0.01:
                reasons.append("gain budget")
    return {"gain_db": gain, "target_lufs": preset["target_lufs"],
            "true_peak_ceiling_dbtp": preset["true_peak_db"],
            "max_boost_db": preset["max_boost_db"], "limited_by": reasons}


def stage_levels(stats):
    def finite(value):
        value = float(value)
        return value if math.isfinite(value) else None
    return {"integrated_lufs": finite(stats["input_i"]),
            "true_peak_dbtp": finite(stats["input_tp"])}


def compressor_measurement(before, after, rate, channels):
    """Compare aligned float samples in 50 ms windows; no internal GR meter claim.

    A compressor without makeup gain or mixing only attenuates these samples.
    RMS ratios measure its effective attenuation even during attack/release.
    Windows below -80 dBFS RMS at the input are excluded from the averages.
    """
    processes, errors = [], []
    block_samples = round(rate * 0.05) * channels
    weighted_gr = maximum_gr = affected_samples = active_samples = 0
    try:
        for path in (before, after):
            error = tempfile.TemporaryFile()
            errors.append(error)
            processes.append(subprocess.Popen([
                pa.tool("ffmpeg"), "-v", "error", "-nostdin", "-i", str(path),
                "-map", "0:a:0", "-c:a", "pcm_f32le", "-f", "f32le", "-"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=error))
        while True:
            blocks = [p.stdout.read(block_samples * 4) for p in processes]
            if len(blocks[0]) != len(blocks[1]) or len(blocks[0]) % (4 * channels):
                raise pa.Error("Compressor measurement found unequal or unaligned audio lengths.")
            if not blocks[0]:
                break
            energies = []
            count = len(blocks[0]) // 4
            for block in blocks:
                samples = array.array("f")
                samples.frombytes(block)
                if sys.byteorder != "little":
                    samples.byteswap()
                energy = sum(sample * sample for sample in samples)
                if not math.isfinite(energy):
                    raise pa.Error("Non-finite samples in compressor measurement.")
                energies.append(energy)
            if energies[0] / count < 1e-8:
                continue
            reduction = max(0.0, 10 * math.log10(energies[0] / max(energies[1], 1e-30)))
            weighted_gr += reduction * count
            active_samples += count
            maximum_gr = max(maximum_gr, reduction)
            if reduction >= 0.1:
                affected_samples += count
        for process, error in zip(processes, errors):
            if process.wait():
                error.seek(0)
                raise pa.Error(error.read().decode(errors="replace"))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.stdout.close()
            process.wait()
        for error in errors:
            error.close()
    return {"method": "RMS attenuation in aligned 50 ms windows before spatial effects/final gain",
            "window_ms": 50, "input_gate_dbfs": -80,
            "measured_audio_seconds": active_samples / (rate * channels),
            "mean_attenuation_db": weighted_gr / active_samples if active_samples else None,
            "max_attenuation_db": maximum_gr if active_samples else None,
            "percent_time_attenuated_at_least_0_1_db":
                100 * affected_samples / active_samples if active_samples else None}


def format_report(report):
    def number(value):
        return "n/a" if value is None else f"{value:.2f}"
    lines = ["PERFORMANCE AUDIO REPORT", f"Video: {report['output']}",
             f"Preset: {report['preset_name']}",
             "Order: high-pass/EQ -> input matching -> compression -> parallel ambience + reverb -> final normalization", "",
             "Stage                          LUFS       True peak dBTP"]
    for name, title in (("after_eq", "After EQ / before matching"),
                        ("after_input_matching", "After input level matching"),
                        ("after_compression", "After compression"),
                        ("after_spatial_effects", "After ambience + reverb"), ("output", "Final encoded output")):
        levels = report["levels"][name]
        lines.append(f"{title:29} {number(levels['integrated_lufs']):>8} {number(levels['true_peak_dbtp']):>17}")
    lines.append("\nn/a means silence or loudness below the measurement gate.")
    settings = report["settings"]
    eq_description = [f"high-pass {settings['highpass_hz']:g} Hz"] if settings["highpass_hz"] else []
    eq_description += [f"{b['frequency_hz']:g} Hz {b['gain_db']:+g} dB (Q {b['q']:g})"
                       for b in settings.get("eq_bands", [])]
    lines.append("EQ: " + ("; ".join(eq_description) if eq_description else "bypassed"))
    lines.append("Spatial routing: dry + ambience return + reverb return (parallel, dry gain fixed at 1).")
    for name in ("ambience", "reverb"):
        amount = settings[f"{name}_mix"]
        lines.append(f"{name.title()}: " + (
            f"{100 * amount:g}% return level relative to dry; "
            f"{settings[f'{name}_decay_seconds']:g} s decay; {settings[f'{name}_predelay_ms']:g} ms predelay"
            if amount else "bypassed"))
    for key, title in (("input_matching", "Input gain"), ("final_normalization", "Final gain")):
        stage = report[key]
        limits = "; limited by " + ", ".join(stage["limited_by"]) if stage["limited_by"] else ""
        lines.append(f"{title}: {stage['gain_db']:+.2f} dB (target {stage['target_lufs']:.1f} LUFS{limits})")
    compressor = report["compressor"]
    if not compressor["enabled"]:
        lines.append("Compressor: bypassed (ratio 1:1); no compression applied.")
    elif compressor["mean_attenuation_db"] is None:
        lines.append("Compressor: no input windows above -80 dBFS RMS to measure.")
    else:
        lines += [f"Compressor: {compressor['ratio']:g}:1, threshold {compressor['threshold_dbfs']:g} dBFS",
                  f"  Mean attenuation: {compressor['mean_attenuation_db']:.2f} dB",
                  f"  Maximum attenuation: {compressor['max_attenuation_db']:.2f} dB",
                  f"  Time with at least 0.1 dB attenuation: {compressor['percent_time_attenuated_at_least_0_1_db']:.1f}%",
                  "  Measured over 50 ms RMS windows before ambience/reverb; windows below -80 dBFS excluded.",
                  "  These are effective audio attenuation measurements, not an instantaneous plugin meter."]
        if compressor["max_attenuation_db"] < 0.1:
            lines.append("  The compressor had little or no measurable effect on this recording.")
    return "\n".join(lines) + "\n"


def save_report(result, report):
    """Do not overwrite preexisting sidecars, even if their video was removed."""
    paths = []
    for extension, content in (("txt", format_report(report)),
                               ("json", json.dumps(report, indent=2, allow_nan=False) + "\n")):
        number = 1
        while True:
            suffix = "" if number == 1 else f"-{number}"
            path = result.with_name(f"{result.name}.audio-report{suffix}.{extension}")
            try:
                with path.open("x") as file:
                    file.write(content)
                paths.append(path)
                break
            except FileExistsError:
                number += 1
    return paths


def process(video, preset_name=None, config=None, share=False, audio_track=0,
            reverb=None, ambience=None):
    data = load_presets(config)
    name = preset_name or data["default"]
    if name not in data["presets"]:
        raise pa.Error(f"Unknown preset {name!r}; choose from {', '.join(data['presets'])}.")
    preset = dict(data["presets"][name])
    for effect, value in (("ambience", ambience), ("reverb", reverb)):
        if value is not None:
            if not math.isfinite(value) or not 0 <= value <= 50:
                raise pa.Error(f"--{effect} must be a percentage between 0 and 50.")
            preset[f"{effect}_mix"] = value / 100
    video = video.resolve()
    before = pa.fingerprint(video)
    info = pa.probe(video)
    v, a = pa.stream(info, "video"), pa.stream(info, "audio", audio_track)
    if a.get("channels") not in (1, 2):
        raise pa.Error("Automatic effects support mono/stereo audio. Select a mono/stereo "
                       "track with --audio-track; spatial/surround downmixing is not automatic.")
    rate = int(a["sample_rate"])
    length = pa.duration(info, v)
    offset = float(a.get("start_time", 0)) - float(v.get("start_time", 0))
    filters = ["asetpts=PTS-STARTPTS", f"aresample={rate}"]
    if offset > 0:
        filters.append(f"adelay={round(offset * rate)}S:all=1")
    elif offset < 0:
        filters += [f"atrim=start={-offset:.9f}", "asetpts=PTS-STARTPTS"]
    filters += eq_filters(preset, rate)
    # Keep processing on the video timeline, including any silent tail.
    filters += ["apad", f"atrim=duration={length:.9f}"]
    print(f"{video.name}: applying {preset['label']}...", flush=True)
    with tempfile.TemporaryDirectory(prefix="performance-effects-") as temporary:
        folder = Path(temporary)
        highpassed = folder / "highpassed.wav"
        pa.publish(["-i", video, "-map", f"0:{a['index']}", "-af", ",".join(filters),
                    "-c:a", "pcm_f32le", "-ar", str(rate), "-rf64", "auto"], highpassed)
        before_matching = loudness(highpassed)
        input_settings = {**preset, "target_lufs": preset["input_target_lufs"],
                          "true_peak_db": preset["input_true_peak_db"]}
        input_gain = gain_for(before_matching, input_settings)
        print(f"Input level matching: {before_matching['input_i']} LUFS, "
              f"gain {input_gain:+.2f} dB.", flush=True)
        matched = highpassed
        if abs(input_gain) > 1e-8:
            matched = folder / "matched.wav"
            pa.publish(["-i", highpassed, "-af", f"volume={input_gain:.8f}dB",
                        "-c:a", "pcm_f32le", "-rf64", "auto"], matched)
        after_matching = loudness(matched) if matched != highpassed else before_matching
        compressed = matched
        compressor = {"enabled": preset["compressor_ratio"] > 1,
                      "ratio": preset["compressor_ratio"],
                      "threshold_dbfs": preset["compressor_threshold_db"],
                      "attack_ms": 20, "release_ms": 250, "makeup_gain_db": 0}
        if compressor["enabled"]:
            print("Compressing and measuring attenuation...", flush=True)
            compressed = folder / "compressed.wav"
            pa.publish(["-i", matched, "-af",
                        f"acompressor=threshold={preset['compressor_threshold_db']}dB:"
                        f"ratio={preset['compressor_ratio']}:attack=20:release=250:"
                        "makeup=1:level_in=1:mix=1:detection=rms:link=average",
                        "-c:a", "pcm_f32le", "-rf64", "auto"], compressed)
            compressor.update(compressor_measurement(matched, compressed, rate, a["channels"]))
        after_compression = loudness(compressed) if compressed != matched else after_matching
        processed = spatial_effects(compressed, folder, preset, rate, length)
        after_spatial = loudness(processed) if processed != compressed else after_compression
        # Do not turn the existing +18 dB cap into two independent +18 dB boosts.
        final_settings = {**preset, "max_boost_db": max(0, preset["max_boost_db"] - max(0, input_gain))}
        gain = gain_for(after_spatial, final_settings)
        print(f"Final normalization: {after_spatial['input_i']} LUFS, gain {gain:+.2f} dB.", flush=True)
        if pa.fingerprint(video) != before:
            raise pa.Error("Source changed during effects processing; no video was published.")
        suffix = ".mp4" if share else ".mov"
        output = video.with_name(f"{video.stem}-{name}{suffix}")
        result = pa.replace(video, processed, output, share=share, numbered=True,
                            audio_filter=f"volume={gain:.8f}dB")
        report = {"version": 3, "source": str(video), "output": str(result),
                  "preset_name": name, "settings": preset,
                  "sample_rate": rate, "channels": a["channels"], "duration_seconds": length,
                  "levels": {"after_eq": stage_levels(before_matching),
                             "after_input_matching": stage_levels(after_matching),
                             "after_compression": stage_levels(after_compression),
                             "after_spatial_effects": stage_levels(after_spatial),
                             "output": stage_levels(loudness(result))},
                  "input_matching": gain_details(before_matching, input_settings, input_gain),
                  "compressor": compressor,
                  "spatial_effects": {"routing": "parallel_returns", "dry_gain": 1,
                      "ambience_return_gain": preset["ambience_mix"],
                      "reverb_return_gain": preset["reverb_mix"]},
                  "final_normalization": gain_details(after_spatial, final_settings, gain)}
        paths = save_report(result, report)
        print(format_report(report), flush=True)
        print("Reports: " + ", ".join(str(p) for p in paths), flush=True)
    print(f"Created: {result}", flush=True)
    return result


def select_videos(use_finder=False):
    # JSON avoids ambiguous newline/tab delimiters in actual file names.
    script = '''ObjC.import('Foundation');
const app = Application.currentApplication(); app.includeStandardAdditions = true;
let paths = [];
if (USE_FINDER) {
  const finder = Application('com.apple.finder');
  if (finder.running()) paths = finder.selection().map(f =>
    ObjC.unwrap($.NSURL.URLWithString(f.url()).path));
  paths = paths.filter(p => /\\.(mov|mp4|m4v)$/i.test(p));
}
if (!paths.length) {
  try {
    paths = app.chooseFile({withPrompt:'Choose performance videos',
      ofType:['public.movie'], multipleSelectionsAllowed:true}).map(p=>p.toString());
  } catch (e) { if (e.errorNumber === -128) paths = []; else throw e; }
}
JSON.stringify(paths);'''.replace("USE_FINDER", "true" if use_finder else "false")
    return [Path(p) for p in json.loads(pa.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", script]))]


def notify(message):
    script = ('on run argv\n'
              'display notification (item 1 of argv) with title "Performance Audio"\n'
              'end run')
    # Best effort: notification permissions do not determine processing success.
    subprocess.run(["/usr/bin/osascript", "-e", script, message], capture_output=True)


def run_process(args):
    load_presets(args.config)  # Fail before opening a picker or starting a job.
    if args.background:
        script_dir = Path(__file__).resolve().parent
        app = next((p for p in script_dir.parents if p.suffix == ".app"), None)
        # Runtime files must never change the resources of a signed app.
        log_dir = (app.parent if app else script_dir) / "performance-audio-jobs"
        log_dir.mkdir(exist_ok=True)
        fd, logfile = tempfile.mkstemp(prefix="job-", suffix=".log", dir=log_dir)
        command = [sys.executable, str(Path(pa.__file__).resolve()), "process"]
        if args.preset:
            command += ["--preset", args.preset]
        if args.config:
            command += ["--config", str(args.config.resolve())]
        command += ["--audio-track", str(args.audio_track)]
        if args.reverb is not None:
            command += ["--reverb", str(args.reverb)]
        if args.ambience is not None:
            command += ["--ambience", str(args.ambience)]
        for field in ("share", "finder_selection", "choose", "reveal", "notify"):
            if getattr(args, field):
                command.append("--" + field.replace("_", "-"))
        command += [str(p.resolve()) for p in args.videos]
        with os.fdopen(fd, "w") as log:
            subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             start_new_session=True)
        print(f"Started. Log: {logfile}")
        return
    videos = args.videos
    if not videos and (args.finder_selection or args.choose):
        videos = select_videos(args.finder_selection)
    if not videos:
        if args.finder_selection or args.choose:
            return  # User cancelled the picker.
        raise pa.Error("Provide videos, --choose, or --finder-selection.")
    errors, completed = [], []
    for video in videos:
        try:
            result = process(video, args.preset, args.config, args.share,
                             args.audio_track, args.reverb, args.ambience)
            completed.append(result)
            if args.reveal:
                try:
                    pa.run(["/usr/bin/open", "-R", result])
                except pa.Error as error:
                    print(f"Created successfully but Finder reveal failed: {error}", file=sys.stderr)
        except (pa.Error, OSError, ValueError) as error:
            errors.append(f"{video.name}: {error}")
            print(errors[-1], file=sys.stderr, flush=True)
    if errors:
        # main() emits the one failure notification; don't also send a conflicting
        # completion notification for the same job.
        raise pa.Error(f"Created {len(completed)} video(s); {len(errors)} failed.\n" + "\n".join(errors))
    if args.notify:
        notify(f"Created {len(completed)} video(s).")
