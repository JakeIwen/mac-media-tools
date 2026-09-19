"""Run with macbook/build/bpm-venv/bin/python -m unittest discover ..."""
import csv
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bpm_over_time as bpm
import numpy as np


RATE = bpm.SAMPLE_RATE


def clicks(duration, tempo):
    samples = np.zeros(round(duration * RATE), dtype=np.float32)
    rng = np.random.default_rng(72)
    pulse = (rng.normal(0, 0.4, round(RATE * 0.025)) *
             np.exp(-np.arange(round(RATE * 0.025))/(RATE*0.006))).astype(np.float32)
    t = 0.3
    while t < duration:
        start = round(t * RATE); count = min(len(pulse), len(samples)-start)
        samples[start:start+count] += pulse[:count]
        t += 60 / (tempo(t) if callable(tempo) else tempo)
    return samples


class BPMTests(unittest.TestCase):
    def setUp(self):
        bpm.configure_tools()
        self.temp = tempfile.TemporaryDirectory(prefix="bpm-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def wav(self, name, samples):
        path = self.root / name
        with wave.open(str(path), "wb") as file:
            file.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
            file.writeframes((np.clip(samples, -1, 1)*32767).astype("<i2").tobytes())
        return path

    def test_known_tempos_have_sixty_points_and_overlapping_windows(self):
        for tempo in (60, 90, 120, 180):
            with self.subTest(tempo=tempo):
                result = bpm.analyze_samples(clicks(20, tempo))
                self.assertEqual(len(result["points"]), 60)
                self.assertGreaterEqual(result["valid_points"], 56)
                self.assertAlmostEqual(result["median_bpm"], tempo, delta=1.5)
                self.assertEqual(result["window_seconds"], 8)
                self.assertEqual(result["points"][0]["window_start_s"], 0)
                self.assertEqual(result["points"][-1]["window_end_s"], 20)
                self.assertLess(result["points"][1]["window_start_s"], result["points"][0]["window_end_s"])

    def test_step_change_is_visible_without_blending_half_and_double_tempo(self):
        result = bpm.analyze_samples(clicks(40, lambda t: 90 if t < 20 else 120))
        left = [p["bpm"] for p in result["points"] if p["time_s"] < 15 and p["bpm"]]
        right = [p["bpm"] for p in result["points"] if p["time_s"] > 25 and p["bpm"]]
        self.assertAlmostEqual(np.median(left), 90, delta=2)
        self.assertAlmostEqual(np.median(right), 120, delta=2)

    def test_gradual_tempo_drift_tracks_the_local_rate(self):
        result = bpm.analyze_samples(clicks(60, lambda t: 80 + t))
        middle = [p for p in result["points"] if 8 < p["time_s"] < 52 and p["bpm"]]
        self.assertGreater(len(middle), 35)
        errors = [abs(p["bpm"] - (80 + p["time_s"])) for p in middle]
        self.assertLess(np.median(errors), 3)
        self.assertGreater(middle[-1]["bpm"], middle[0]["bpm"] + 25)

    def test_silence_and_sustained_tone_are_unestimated(self):
        tone = (0.2*np.sin(2*np.pi*440*np.arange(RATE*12)/RATE)).astype(np.float32)
        for samples in (np.zeros(RATE*12, dtype=np.float32), tone):
            result = bpm.analyze_samples(samples)
            self.assertEqual(result["valid_points"], 0)
            self.assertIsNone(result["median_bpm"])
            self.assertTrue(all(p["bpm"] is None for p in result["points"]))

    def test_long_pause_produces_gaps_not_zero_or_interpolated_tempo(self):
        samples = clicks(40, 100); samples[15*RATE:25*RATE] = 0
        result = bpm.analyze_samples(samples, window_seconds=6)
        middle = [p for p in result["points"] if 18 < p["time_s"] < 22]
        self.assertTrue(all(p["bpm"] is None for p in middle))
        self.assertGreater(result["valid_points"], 30)

    def test_report_exports_and_collision_preserve_original(self):
        source = self.wav("Jake's take & {{DATA}}.wav", clicks(12, 100))
        before = source.read_bytes()
        result = bpm.analyze_file(source)
        folder = result.parent
        data = json.loads((folder / "analysis.json").read_text())
        self.assertEqual(data["point_count"], 60)
        with (folder / "bpm-data.csv").open() as file:
            self.assertEqual(len(list(csv.DictReader(file))), 60)
        self.assertGreater((folder / "bpm-over-time.png").stat().st_size, 1000)
        self.assertIn("tempo-point-", (folder / "bpm-over-time.svg").read_text())
        self.assertIn("&amp; {{DATA}}.wav", result.read_text())
        second = bpm.analyze_file(source)
        self.assertNotEqual(second, result)
        self.assertEqual(source.read_bytes(), before)

    def test_untrusted_names_cannot_inject_script_into_report(self):
        report = bpm.analyze_samples(clicks(8, 120))
        report["source"] = 'bad<script>alert(1){{SVG}}.wav'
        text = bpm.html_report(report, '<svg id="safe"></svg>')
        self.assertNotIn('<script>alert(1)</script>', text)
        self.assertIn('&lt;script&gt;', text)
        self.assertIn('\\u003cscript\\u003e', text)
        self.assertEqual(text.count('<svg id="safe">'), 1)

    def test_video_audio_offset_is_present_in_analysis_timeline(self):
        audio = self.wav("clicks.wav", clicks(12, 120))
        video = self.root / "offset.mov"
        bpm.media.run([bpm.media.tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
            "color=size=64x64:rate=30:duration=14", "-itsoffset", "2", "-i", audio,
            "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-c:a", "pcm_s16le", video])
        scratch = self.root / "decode"; scratch.mkdir()
        samples, _ = bpm.decode(video, scratch, 0)
        self.assertAlmostEqual(len(samples)/RATE, 14, delta=0.02)
        self.assertLess(np.max(np.abs(samples[:2*RATE])), 1e-6)
        self.assertGreater(np.max(np.abs(samples[2*RATE:])), 0.1)

    def test_cli_rejects_invalid_analysis_parameters(self):
        for options in (("--points", "1"), ("--window-seconds", "nan"), ("--min-bpm", "250", "--max-bpm", "100")):
            result = subprocess.run([sys.executable, str(Path(bpm.__file__)), *options], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)

    def test_starting_hint_does_not_prevent_later_acceleration_or_slowing(self):
        for start, end in ((140, 200), (180, 120)):
            with self.subTest(start=start):
                tempo = lambda t: start if t < 22 else start + (end-start)*min(1,(t-22)/25)
                result = bpm.analyze_samples(clicks(50, tempo), bpm_hint=start)
                early = [p['bpm'] for p in result['points'] if 5<p['time_s']<17 and p['bpm']]
                late = [p['bpm'] for p in result['points'] if 44<p['time_s'] and p['bpm']]
                self.assertAlmostEqual(np.median(early), start, delta=3)
                self.assertAlmostEqual(np.median(late), end, delta=9)

    def test_alternating_weak_beats_do_not_force_half_time_with_starting_hint(self):
        samples = clicks(32, 140)
        # Strong/weak alternation makes two-beat repetitions stronger than a
        # single beat. The starting hint identifies which metrical level to track.
        for number, t in enumerate(np.arange(.3, 32, 60/140)):
            if number % 2:
                start = round(t*RATE)
                samples[start:start+round(.025*RATE)] *= 0.3
        result = bpm.analyze_samples(samples, bpm_hint=140)
        self.assertGreater(result['valid_points'], 50)
        self.assertAlmostEqual(result['median_bpm'], 140, delta=2)
        self.assertTrue(all(p['local_bpm'] is not None for p in result['points'] if p['bpm']))


if __name__ == "__main__": unittest.main()
