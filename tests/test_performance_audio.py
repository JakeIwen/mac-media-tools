#!/usr/bin/env python3
"""Integration tests using tiny generated media; requires ffmpeg and ffprobe."""

import importlib.util
import errno
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "performance_audio.py"
spec = importlib.util.spec_from_file_location("performance_audio", SCRIPT)
pa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pa)


class PerformanceAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg, cls.ffprobe = pa.tool("ffmpeg"), pa.tool("ffprobe")
        cls.fixtures = tempfile.TemporaryDirectory(prefix="performance-fixtures-")
        root = Path(cls.fixtures.name)
        raw = root / "raw.mov"
        cls.video = root / "portrait.mov"
        cls.audio = root / "edited.wav"
        cls.ff("-f", "lavfi", "-i", "testsrc2=size=160x96:rate=30:duration=3",
               "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=3",
               "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=3",
               "-map", "0:v", "-map", "1:a", "-map", "2:a", "-c:v", "libx264",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-metadata",
               "creation_time=2026-09-17T12:00:00Z", raw)
        cls.ff("-display_rotation", "90", "-i", raw, "-map", "0", "-c", "copy",
               "-metadata", "creation_time=2026-09-17T12:00:00Z", cls.video)
        cls.ff("-f", "lavfi", "-i", "sine=frequency=660:sample_rate=44100:duration=3",
               "-c:a", "pcm_s24le", cls.audio)

    @classmethod
    def tearDownClass(cls):
        cls.fixtures.cleanup()

    @classmethod
    def ff(cls, *args):
        return pa.run([cls.ffmpeg, "-v", "error", "-nostdin", "-y", *args])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="performance-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Shell metacharacters must be treated as filename characters throughout.
        self.video = self.root / "Jacob's take $(touch OOPS) & music.MOV"
        self.audio = self.root / "edited.wav"
        shutil.copy2(self.__class__.video, self.video)
        shutil.copy2(self.__class__.audio, self.audio)

    def packets(self, path):
        data = json.loads(pa.run([self.ffprobe, "-v", "error", "-select_streams", "v:0",
                                  "-show_packets", "-show_data_hash", "sha256",
                                  "-of", "json", path]))
        return [p["data_hash"] for p in data["packets"]]

    def audio_hash(self, path):
        return self.ff("-i", path, "-map", "0:a:0", "-c:a", "pcm_s24le",
                       "-f", "hash", "-")

    def test_lossless_replacement_preserves_video_and_replaces_all_audio(self):
        before = pa.fingerprint(self.video)
        result = pa.replace(self.video, self.audio)
        info = pa.probe(result)
        self.assertEqual(self.packets(self.video), self.packets(result))
        self.assertEqual(self.audio_hash(self.audio), self.audio_hash(result))
        self.assertEqual(sum(s["codec_type"] == "audio" for s in info["streams"]), 1)
        self.assertEqual(pa.stream(info, "audio")["sample_rate"], "44100")
        self.assertEqual(pa.rotation(pa.stream(info, "video")), 90)
        self.assertIn("2026-09-17T12:00:00", info["format"]["tags"]["creation_time"])
        self.assertEqual(pa.fingerprint(self.video), before)

    def test_prepare_finish_and_numbered_outputs(self):
        project = pa.prepare(self.video, audio_track=1, silent=True)
        extracted = pa.probe(project / "original.wav")
        self.assertEqual(pa.stream(extracted, "audio")["sample_rate"], "48000")
        self.assertAlmostEqual(pa.duration(extracted, pa.stream(extracted, "audio")), 3, places=3)
        expected = self.root / "second-track.wav"
        self.ff("-i", self.video, "-map", "0:a:1", "-t", "3", "-c:a", "pcm_s24le", expected)
        self.assertEqual(self.audio_hash(expected), self.audio_hash(project / "original.wav"))
        self.assertEqual(self.packets(self.video), self.packets(project / "silent.mov"))
        self.assertFalse(any(s["codec_type"] == "audio" for s in pa.probe(project / "silent.mov")["streams"]))
        shutil.copy2(self.audio, project / "edited.wav")
        first = pa.finish(project)
        original_bytes = first.read_bytes()
        second = pa.finish(project)
        self.assertEqual(first.name, "finished.mov")
        self.assertEqual(second.name, "finished-2.mov")
        self.assertEqual(first.read_bytes(), original_bytes)
        for launcher in project.glob("*.command"):
            subprocess.run(["/bin/zsh", "-n", str(launcher)], check=True)
        subprocess.run(["/bin/zsh", str(project / "Finish.command")], input="\n",
                       text=True, capture_output=True, check=True)
        self.assertTrue((project / "finished-3.mov").is_file())
        self.assertFalse((project / "OOPS").exists())

    def test_share_keeps_video_packets_and_uses_aac(self):
        result = pa.replace(self.video, self.audio, share=True)
        self.assertEqual(result.suffix, ".mp4")
        self.assertEqual(self.packets(self.video), self.packets(result))
        self.assertEqual(pa.stream(pa.probe(result), "audio")["codec_name"], "aac")

    def test_nonzero_video_start_is_aligned_with_edited_audio(self):
        offset = self.root / "offset.mov"
        self.ff("-itsoffset", "0.5", "-i", self.video, "-i", self.video,
                "-map", "0:v", "-map", "1:a:0", "-c", "copy", offset)
        self.assertAlmostEqual(float(pa.stream(pa.probe(offset), "video")["start_time"]), 0.5)
        result = pa.replace(offset, self.audio)
        info = pa.probe(result)
        self.assertAlmostEqual(float(pa.stream(info, "video")["start_time"]), 0)
        self.assertAlmostEqual(float(pa.stream(info, "audio")["start_time"]), 0)
        self.assertEqual(self.packets(offset), self.packets(result))

    def test_mismatch_rejected_without_output_and_override_keeps_video(self):
        short = self.root / "short.wav"
        self.ff("-i", self.audio, "-t", "1", "-c:a", "copy", short)
        output = self.root / "output.mov"
        with self.assertRaisesRegex(pa.Error, "Duration mismatch"):
            pa.replace(self.video, short, output)
        self.assertFalse(output.exists())
        result = pa.replace(self.video, short, output, allow_mismatch=True)
        self.assertEqual(self.packets(self.video), self.packets(result))

    def test_explicit_outputs_never_overwrite_sources_or_results(self):
        before = self.video.read_bytes()
        with self.assertRaises(pa.Error):
            pa.replace(self.video, self.audio, self.video)
        self.assertEqual(before, self.video.read_bytes())
        output = self.root / "existing.mov"
        output.write_text("precious")
        with self.assertRaisesRegex(pa.Error, "overwrite"):
            pa.replace(self.video, self.audio, output)
        self.assertEqual(output.read_text(), "precious")
        self.assertFalse(list(self.root.glob(".performance-*")))

    def test_ambiguous_audio_and_changed_source_are_rejected(self):
        project = pa.prepare(self.video)
        shutil.copy2(self.audio, project / "edited.wav")
        shutil.copy2(self.audio, project / "edited.aiff")
        with self.assertRaisesRegex(pa.Error, "found 2"):
            pa.finish(project)
        (project / "edited.aiff").unlink()
        with self.video.open("ab") as file:
            file.write(b"changed")
        with self.assertRaisesRegex(pa.Error, "has changed"):
            pa.finish(project)

    def test_invalid_input_cleans_temporary_output(self):
        output = self.root / "output.mov"
        with self.assertRaises(pa.Error):
            pa.publish(["-i", self.root / "missing.mov"], output)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob(".performance-*")))

    @unittest.skipUnless(sys.platform == "darwin", "macOS exclusive rename")
    def test_mac_publication_does_not_require_hard_links(self):
        with patch.object(pa.os, "link", side_effect=PermissionError(errno.EPERM, "hard links denied")) as link:
            result = pa.replace(self.video, self.audio)
        link.assert_not_called()
        self.assertEqual(self.packets(self.video), self.packets(result))
        self.assertEqual(self.audio_hash(self.audio), self.audio_hash(result))
        self.assertFalse(list(self.root.glob(".performance-*")))

    def test_exclusive_publication_race_and_dangling_symlink(self):
        target = self.root / "destination.mov"
        a, b = self.root / "one.mov", self.root / "two.mov"
        a.write_bytes(b"first"); b.write_bytes(b"second")
        def publish(source):
            try:
                pa.move_no_replace(source, target)
                return True
            except FileExistsError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(publish, (a, b)))
        self.assertEqual(sum(results), 1)
        self.assertIn(target.read_bytes(), (b"first", b"second"))
        self.assertEqual(sum(p.exists() for p in (a, b)), 1)
        symlink = self.root / "dangling.mov"
        symlink.symlink_to(self.root / "missing-target")
        remaining = a if a.exists() else b
        with self.assertRaises(FileExistsError):
            pa.move_no_replace(remaining, symlink)
        self.assertTrue(symlink.is_symlink())
        self.assertTrue(remaining.is_file())

    def test_failed_final_publication_retains_validated_movie(self):
        with patch.object(pa, "move_no_replace", side_effect=PermissionError(errno.EPERM, "denied")):
            with self.assertRaisesRegex(pa.Error, "validated video is retained"):
                pa.replace(self.video, self.audio)
        saved = list(self.root.glob(".performance-*.mov"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(self.packets(self.video), self.packets(saved[0]))
        self.assertEqual(self.audio_hash(self.audio), self.audio_hash(saved[0]))

    def test_watch_waits_for_export_and_finishes_once(self):
        project = pa.prepare(self.video)
        process = subprocess.Popen([sys.executable, str(SCRIPT), "watch", str(project),
                                    "--settle", "2", "--once"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            # Simulate an export with an incomplete header before the real file.
            target = project / "edited.wav"
            target.write_bytes(b"incomplete")
            time.sleep(1)
            self.assertFalse((project / "finished.mov").exists())
            shutil.copy2(self.audio, target)
            stdout, stderr = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            self.assertEqual(self.audio_hash(target), self.audio_hash(project / "finished.mov"))
            self.assertFalse((project / "finished-2.mov").exists())
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=5)

    def test_float_pcm_and_apple_lossless_are_not_quantized(self):
        for codec, extension in (("pcm_f32le", ".wav"), ("alac", ".m4a")):
            with self.subTest(codec=codec):
                audio = self.root / (codec + extension)
                self.ff("-i", self.audio, "-c:a", codec, audio)
                result = pa.replace(self.video, audio)
                self.assertEqual(pa.stream(pa.probe(result), "audio")["codec_name"], codec)
                self.assertEqual(self.audio_hash(audio), self.audio_hash(result))

    def test_hevc_hdr_signaling_and_packets_survive(self):
        video = self.root / "HDR.mov"
        self.ff("-f", "lavfi", "-i", "testsrc2=size=128x96:rate=24:duration=3",
                "-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le",
                "-x265-params", "log-level=error:pools=1:colorprim=9:transfer=18:colormatrix=9",
                "-tag:v", "hvc1",
                "-color_primaries", "bt2020", "-color_trc", "arib-std-b67",
                "-colorspace", "bt2020nc", video)
        result = pa.replace(video, self.audio)
        self.assertEqual(self.packets(video), self.packets(result))
        track = pa.stream(pa.probe(result), "video")
        self.assertEqual(track["color_transfer"], "arib-std-b67")
        self.assertEqual(track["pix_fmt"], "yuv420p10le")
        self.assertEqual(track["codec_tag_string"], "hvc1")


if __name__ == "__main__":
    unittest.main()
