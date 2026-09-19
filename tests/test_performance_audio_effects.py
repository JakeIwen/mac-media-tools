"""Generated-media tests for automatic audio presets (requires FFmpeg)."""

import array
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import performance_audio as pa
import performance_audio_effects as fx


class EffectsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="performance-dsp-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "take's audio.mov"
        self.ff("-f", "lavfi", "-i", "testsrc2=size=128x96:rate=24:duration=4",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
                "-c:v", "libx264", "-c:a", "pcm_s24le", "-ac", "2", self.video)

    def ff(self, *args):
        return pa.run([pa.tool("ffmpeg"), "-v", "error", "-nostdin", "-y", *args])

    def packets(self, file):
        data = json.loads(pa.run([pa.tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
                                  "-show_packets", "-show_data_hash", "sha256", "-of", "json", file]))
        return [p["data_hash"] for p in data["packets"]]

    def test_clean_normalizes_without_touching_video_or_audio_timing(self):
        before = pa.fingerprint(self.video)
        result = fx.process(self.video, "clean")
        info = pa.probe(result)
        audio = pa.stream(info, "audio")
        stats = fx.loudness(result)
        self.assertAlmostEqual(float(stats["input_i"]), -16, delta=0.1)
        self.assertLessEqual(float(stats["input_tp"]), -1.49)
        self.assertEqual(self.packets(self.video), self.packets(result))
        self.assertAlmostEqual(pa.duration(info, audio), 4, places=3)
        self.assertEqual(audio["sample_rate"], "48000")
        self.assertEqual(audio["channels"], 2)
        self.assertEqual(audio["codec_name"], "pcm_s24le")
        self.assertEqual(pa.fingerprint(self.video), before)

    def test_all_presets_and_share_preserve_video(self):
        for name in ("subtle", "fuller"):
            with self.subTest(name=name):
                result = fx.process(self.video, name, share=True)
                self.assertEqual(self.packets(self.video), self.packets(result))
                info = pa.probe(result)
                self.assertEqual(pa.stream(info, "audio")["codec_name"], "aac")
                self.assertAlmostEqual(pa.duration(info, pa.stream(info, "video")), 4, places=3)

    def test_reverb_produces_tail_and_keeps_dry_onset(self):
        source = self.root / "burst.mov"
        self.ff("-i", self.video, "-f", "lavfi", "-i",
                r"aevalsrc=if(lt(t\,0.1)\,0.2*sin(2*PI*440*t)\,0):s=48000:d=4",
                "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "pcm_f32le", source)
        dry = fx.process(source, "clean")
        wet = fx.process(source, "clean", reverb=20)

        def samples(file):
            output = subprocess.check_output([pa.tool("ffmpeg"), "-v", "error", "-i", str(file),
                                              "-map", "0:a:0", "-c:a", "pcm_f32le", "-f", "f32le", "-"])
            values = array.array("f")
            values.frombytes(output)
            if sys.byteorder != "little":
                values.byteswap()
            return values

        a, b = samples(dry), samples(wet)
        self.assertEqual(len(a), len(b))
        self.assertEqual(len(b), 4 * 48000)
        self.assertLess(max(abs(v) for v in a[12000:24000]), 1e-6)
        self.assertGreater(max(abs(v) for v in b[12000:24000]), 1e-4)
        # Dry sound remains at the very beginning; convolution must not delay it.
        self.assertGreater(max(abs(v) for v in b[:480]), 1e-3)

    def test_silence_and_peak_limited_gain(self):
        preset = fx.load_presets()["presets"]["clean"]
        self.assertEqual(fx.gain_for({"input_i": "-inf", "input_tp": "-inf"}, preset), 0)
        self.assertAlmostEqual(fx.gain_for({"input_i": "-30", "input_tp": "-3"}, preset), 1.5)
        self.assertEqual(fx.gain_for({"input_i": "-60", "input_tp": "-40"}, preset), 18)
        source = self.root / "silent-audio.mov"
        self.ff("-i", self.video, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                "-map", "0:v", "-map", "1:a", "-t", "4", "-c:v", "copy", "-c:a", "pcm_s24le", source)
        result = fx.process(source, "fuller")
        self.assertEqual(fx.loudness(result)["input_tp"], "-inf")
        self.assertIsNone(self.report(result)["compressor"]["mean_attenuation_db"])

    def test_invalid_config_and_unknown_preset_fail_cleanly(self):
        config = self.root / "invalid.json"
        data = fx.load_presets()
        data["presets"]["subtle"]["reverb_mix"] = 99
        config.write_text(json.dumps(data))
        with self.assertRaisesRegex(pa.Error, "reverb_mix"):
            fx.process(self.video, config=config)
        result = subprocess.run([sys.executable, str(Path(pa.__file__)), "process", str(self.video),
                                 "--preset", "does-not-exist"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Unknown preset", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(list(self.root.glob("*-subtle.mov")))

    def test_cli_reverb_override_and_numbering(self):
        command = [sys.executable, str(Path(pa.__file__)), "process", str(self.video),
                   "--preset", "clean", "--reverb", "0"]
        for _ in range(2):
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "take's audio-clean.mov").is_file())
        self.assertTrue((self.root / "take's audio-clean-2.mov").is_file())

    def report(self, result):
        return json.loads(result.with_name(result.name + ".audio-report.json").read_text())

    def test_quiet_and_loud_recordings_receive_similar_compression_after_matching(self):
        quiet = self.root / "quiet.mov"
        self.ff("-i", self.video, "-c:v", "copy", "-af", "volume=-12dB",
                "-c:a", "pcm_f32le", quiet)
        normal_result = fx.process(self.video, "fuller")
        quiet_result = fx.process(quiet, "fuller")
        normal, low = self.report(normal_result), self.report(quiet_result)
        self.assertAlmostEqual(low["input_matching"]["gain_db"] - normal["input_matching"]["gain_db"], 12, delta=0.1)
        self.assertAlmostEqual(low["levels"]["after_input_matching"]["integrated_lufs"], -16, delta=0.1)
        self.assertGreater(low["compressor"]["mean_attenuation_db"], 0.1)
        self.assertAlmostEqual(low["compressor"]["mean_attenuation_db"],
                               normal["compressor"]["mean_attenuation_db"], delta=0.05)
        self.assertEqual(self.packets(quiet), self.packets(quiet_result))

    def test_attenuation_measurement_matches_known_gain(self):
        before, after = self.root / "before.wav", self.root / "after.wav"
        self.ff("-i", self.video, "-map", "0:a", "-c:a", "pcm_f32le", before)
        self.ff("-i", before, "-af", "volume=-6dB", "-c:a", "pcm_f32le", after)
        stats = fx.compressor_measurement(before, after, 48000, 2)
        self.assertAlmostEqual(stats["mean_attenuation_db"], 6, delta=0.001)
        self.assertAlmostEqual(stats["max_attenuation_db"], 6, delta=0.001)
        self.assertEqual(stats["percent_time_attenuated_at_least_0_1_db"], 100)
        self.assertAlmostEqual(stats["measured_audio_seconds"], 4)

    def test_bypassed_compressor_and_report_sidecars(self):
        result = fx.process(self.video, "clean")
        report = self.report(result)
        self.assertFalse(report["compressor"]["enabled"])
        self.assertEqual(report["levels"]["after_input_matching"], report["levels"]["after_compression"])
        text = result.with_name(result.name + ".audio-report.txt").read_text()
        self.assertIn("bypassed", text)
        self.assertIn("Final encoded output", text)
        original = result.with_name(result.name + ".audio-report.json").read_bytes()
        second = fx.save_report(result, report)
        self.assertEqual(result.with_name(result.name + ".audio-report.json").read_bytes(), original)
        self.assertTrue(all("audio-report-2" in p.name for p in second))

    def test_boost_cap_is_shared_between_input_and_output(self):
        quiet = self.root / "very-quiet.mov"
        self.ff("-i", self.video, "-c:v", "copy", "-af", "volume=-24dB", "-c:a", "pcm_f32le", quiet)
        report = self.report(fx.process(quiet, "fuller"))
        self.assertAlmostEqual(report["input_matching"]["gain_db"], 18)
        self.assertIn("gain budget", report["input_matching"]["limited_by"])
        self.assertLessEqual(report["input_matching"]["gain_db"] + report["final_normalization"]["gain_db"], 18.001)

    def test_old_presets_receive_input_matching_defaults(self):
        data = fx.load_presets()
        for preset in data["presets"].values():
            for key in fx.OPTIONAL_DEFAULTS:
                preset.pop(key)
        config = self.root / "old.json"
        config.write_text(json.dumps(data))
        self.assertEqual(fx.load_presets(config)["presets"]["fuller"]["input_target_lufs"], -16)
        self.assertEqual(fx.load_presets(config)["presets"]["fuller"]["ambience_mix"], 0)

    def test_jake_preferred_settings_and_round_trip(self):
        settings = fx.load_presets()["presets"]
        jake, baseline = settings["jake-preferred"], settings["fuller"]
        self.assertAlmostEqual((1 - 1 / jake["compressor_ratio"]) /
                               (1 - 1 / baseline["compressor_ratio"]), 1.25)
        self.assertAlmostEqual(jake["reverb_mix"] / baseline["reverb_mix"], 1.4)
        result = fx.process(self.video, "jake-preferred")
        report = self.report(result)
        self.assertEqual(self.packets(self.video), self.packets(result))
        self.assertEqual(report["settings"]["label"], "Jake Preferred")
        self.assertEqual(report["settings"]["eq_bands"], jake["eq_bands"])
        self.assertGreater(report["compressor"]["mean_attenuation_db"], 0.1)
        self.assertIn("after_eq", report["levels"])

    def test_eq_cut_and_presence_boost_are_applied_to_actual_audio(self):
        source = self.root / "two-tones.mov"
        self.ff("-i", self.video, "-f", "lavfi", "-i",
                "aevalsrc=0.05*sin(2*PI*250*t)+0.05*sin(2*PI*3000*t):s=48000:d=4",
                "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "pcm_f32le", source)
        data = fx.load_presets()
        preset = data["presets"]["jake-preferred"]
        # Isolate its actual EQ path: quiet tones need only boost, which is capped
        # at zero; compression/reverb are bypassed for this measurement.
        preset.update(compressor_ratio=1, ambience_mix=0, reverb_mix=0, max_boost_db=0)
        config = self.root / "eq-test.json"
        config.write_text(json.dumps(data))
        result = fx.process(source, "jake-preferred", config)
        report = self.report(result)
        self.assertEqual(report["input_matching"]["gain_db"], 0)
        self.assertEqual(report["final_normalization"]["gain_db"], 0)

        def amplitude(file, frequency):
            raw = subprocess.check_output([pa.tool("ffmpeg"), "-v", "error", "-i", str(file),
                "-map", "0:a:0", "-ss", "1", "-t", "1", "-c:a", "pcm_f32le", "-f", "f32le", "-"])
            samples = array.array("f"); samples.frombytes(raw)
            if sys.byteorder != "little": samples.byteswap()
            step = 2 * math.pi * frequency / 48000
            real = sum(x * math.cos(step * i) for i, x in enumerate(samples))
            imag = sum(x * math.sin(step * i) for i, x in enumerate(samples))
            return 2 * math.hypot(real, imag) / len(samples)

        for frequency, expected in ((250, -2), (3000, 1.5)):
            gain = 20 * math.log10(amplitude(result, frequency) / amplitude(source, frequency))
            self.assertAlmostEqual(gain, expected, delta=0.15)

    def test_invalid_eq_band_is_rejected(self):
        data = fx.load_presets()
        data["presets"]["jake-preferred"]["eq_bands"][0]["gain_db"] = float("nan")
        config = self.root / "invalid-eq.json"
        config.write_text(json.dumps(data))
        with self.assertRaisesRegex(pa.Error, "eq_bands.gain_db"):
            fx.process(self.video, "jake-preferred", config)

    def test_ambience_and_reverb_are_independent_parallel_returns(self):
        source = self.root / "impulse.wav"
        self.ff("-f", "lavfi", "-i", r"aevalsrc=if(eq(n\,480)\,0.25\,0):s=48000:d=2",
                "-ac", "2", "-c:a", "pcm_f32le", source)
        preset = fx.load_presets()["presets"]["jake-preferred"]

        def samples(file):
            raw = subprocess.check_output([pa.tool("ffmpeg"), "-v", "error", "-i", str(file),
                                           "-c:a", "pcm_f32le", "-f", "f32le", "-"])
            values = array.array("f"); values.frombytes(raw)
            if sys.byteorder != "little": values.byteswap()
            return values

        def render(name, ambience, reverb):
            folder = self.root / name; folder.mkdir()
            settings = {**preset, "ambience_mix": ambience, "reverb_mix": reverb}
            return samples(fx.spatial_effects(source, folder, settings, 48000, 2))

        dry = samples(source)
        ambient = render("ambience", 0.12, 0)
        reverb = render("reverb", 0, 0.252)
        both = render("both", 0.12, 0.252)
        self.assertEqual(len(both), len(dry))
        self.assertEqual(len(both), 2 * 48000 * 2)
        error = max(abs(b - a - r + d) for b, a, r, d in zip(both, ambient, reverb, dry))
        self.assertLess(error, 2e-6, "parallel mix must equal dry + isolated returns, not cascaded effects")
        self.assertAlmostEqual(both[480 * 2], dry[480 * 2], places=6)
        early = slice(int(0.018 * 48000) * 2, int(0.034 * 48000) * 2)
        self.assertGreater(max(abs(x) for x in ambient[early]), 0.001)
        self.assertLess(max(abs(x) for x in reverb[early]), 1e-7)
        late = slice(int(0.5 * 48000) * 2, int(0.8 * 48000) * 2)
        self.assertLess(max(abs(x) for x in ambient[late]), 1e-7)
        self.assertGreater(max(abs(x) for x in reverb[late]), 1e-6)

    def test_both_spatial_controls_can_be_bypassed_and_reported(self):
        result = fx.process(self.video, "jake-preferred", ambience=0, reverb=0)
        report = self.report(result)
        self.assertEqual(report["levels"]["after_compression"], report["levels"]["after_spatial_effects"])
        self.assertEqual(report["spatial_effects"]["ambience_return_gain"], 0)
        self.assertEqual(report["spatial_effects"]["reverb_return_gain"], 0)
        text = result.with_name(result.name + ".audio-report.txt").read_text()
        self.assertIn("Ambience: bypassed", text)
        self.assertIn("Reverb: bypassed", text)
        self.assertEqual(self.packets(self.video), self.packets(result))

    def test_cli_independent_ambience_and_reverb_amounts(self):
        result = subprocess.run([sys.executable, str(Path(pa.__file__)), "process", str(self.video),
                                 "--preset", "subtle", "--ambience", "15", "--reverb", "0"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.report(self.root / "take's audio-subtle.mov")
        self.assertEqual(report["settings"]["ambience_mix"], 0.15)
        self.assertEqual(report["settings"]["reverb_mix"], 0)
        self.assertIn("Ambience: 15%", result.stdout)
        self.assertIn("Reverb: bypassed", result.stdout)


if __name__ == "__main__":
    unittest.main()
