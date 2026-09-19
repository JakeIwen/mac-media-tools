"""Tiny generated-media conversions, including audio integrity and iPhone orientation/HDR."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import convert_video as cv
import performance_audio as media


class VideoConversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cv.configure_tools()
        cls.fixtures = tempfile.TemporaryDirectory(prefix="video-convert-fixtures-")
        cls.base = Path(cls.fixtures.name) / "base.mov"
        cls.ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=2",
               "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
               "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast",
               "-c:a", "aac", "-b:a", "192k", cls.base)

    @classmethod
    def tearDownClass(cls): cls.fixtures.cleanup()

    @classmethod
    def ff(cls, *args):
        return media.run([media.tool("ffmpeg"), "-hide_banner", "-v", "error", "-nostdin", "-y", *args])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="video-convert-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "Jake's take $literal.MOV"
        shutil.copy2(self.base, self.video)

    def packets(self, file, selector):
        data = json.loads(media.run([media.tool("ffprobe"), "-v", "error", "-select_streams", selector,
                                    "-show_packets", "-show_data_hash", "sha256", "-of", "json", file]))
        return [p["data_hash"] for p in data["packets"]]

    def test_remux_preserves_video_and_aac_packets_and_original(self):
        before = media.fingerprint(self.video)
        output = cv.convert(self.video, "original")
        self.assertEqual(self.packets(self.video, "v:0"), self.packets(output, "v:0"))
        self.assertEqual(self.packets(self.video, "a:0"), self.packets(output, "a:0"))
        self.assertEqual(media.fingerprint(self.video), before)
        self.assertEqual(output.suffix, ".mp4")
        data = output.read_bytes()
        self.assertLess(data.find(b"moov"), data.find(b"mdat"))
        self.assertNotEqual(cv.convert(self.video, "original"), output)

    def test_resolution_caps_no_upscale_and_portrait_plan(self):
        track = {"width": 3840, "height": 2160, "sample_aspect_ratio": "1:1"}
        for name, size in (("original", (3840,2160)), ("1080p", (1920,1080)),
                           ("720p", (1280,720)), ("540p", (960,540))):
            self.assertEqual(cv.target_size(track, name), size)
        track["side_data_list"] = [{"rotation": 90}]
        self.assertEqual(cv.target_size(track, "1080p"), (1080,1920))
        track = {"width": 640, "height": 480}
        self.assertEqual(cv.target_size(track, "1080p"), (640,480))
        self.assertEqual(cv.target_size({"width":1920,"height":1920}, "1080p"), (1080,1080))

    def test_downscale_video_preserves_aac_packets(self):
        output = cv.convert(self.video, "540p")
        info = media.probe(output)
        self.assertEqual(cv.display_size(media.stream(info,"video")), (960,540))
        self.assertEqual(self.packets(self.video, "a:0"), self.packets(output, "a:0"))
        self.assertEqual(media.stream(info,"video")["codec_name"], "h264")

    def test_portrait_rotation_is_baked_on_resize(self):
        portrait = self.root / "portrait.mov"
        self.ff("-display_rotation", "90", "-i", self.video, "-c", "copy", portrait)
        output = cv.convert(portrait, "540p")
        video = media.stream(media.probe(output), "video")
        self.assertEqual((video["width"], video["height"]), (540,960))
        self.assertEqual(media.rotation(video), 0)
        self.assertEqual(self.packets(portrait,"a:0"), self.packets(output,"a:0"))

    def test_pcm_audio_is_encoded_once_without_effects(self):
        source = self.root / "lossless.mov"
        self.ff("-i", self.video, "-c:v", "copy", "-c:a", "pcm_s24le", source)
        output = cv.convert(source, "original")
        info = media.probe(output)
        audio = media.stream(info,"audio")
        self.assertEqual(audio["codec_name"], "aac")
        self.assertEqual(audio["profile"], "LC")
        self.assertEqual(audio["sample_rate"], "48000")
        self.assertEqual(self.packets(source,"v:0"), self.packets(output,"v:0"))

    def test_no_audio_video_and_repeated_numbering(self):
        source = self.root / "silent.mov"
        self.ff("-i", self.video, "-an", "-c:v", "copy", source)
        output = cv.convert(source,"720p")
        self.assertFalse(any(s["codec_type"]=="audio" for s in media.probe(output)["streams"]))
        self.assertEqual(self.packets(source,"v:0"), self.packets(output,"v:0"))

    def test_hdr_hevc_is_tone_mapped_to_sdr_h264(self):
        hdr = self.root / "HDR.mov"
        self.ff("-f", "lavfi", "-i", "testsrc2=size=128x96:rate=24:duration=2",
                "-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le",
                "-x265-params", "log-level=error:pools=1:colorprim=9:transfer=18:colormatrix=9",
                "-tag:v", "hvc1", hdr)
        output = cv.convert(hdr,"original")
        info = media.probe(output)
        video = media.stream(info,"video")
        self.assertEqual(video["codec_name"],"h264")
        self.assertEqual(video["pix_fmt"],"yuv420p")
        self.assertEqual(video.get("color_transfer"),"bt709")
        self.assertEqual(video.get("color_primaries"),"bt709")
        self.assertEqual((video["width"],video["height"]),(128,96))
        raw = subprocess.check_output([media.tool("ffmpeg"),"-v","error","-i",str(output),
            "-frames:v","1","-pix_fmt","rgb24","-f","rawvideo","-"])
        self.assertGreater(max(raw)-min(raw),100)

    def test_audio_video_start_offset_preserved(self):
        source = self.root / "offset.mov"
        self.ff("-itsoffset","0.2","-i",self.video,"-i",self.video,
                "-map","0:v","-map","1:a","-c","copy",source)
        for resolution in ("original","540p"):
            output=cv.convert(source,resolution)
            info=media.probe(output)
            difference=float(media.stream(info,"video")["start_time"])-float(media.stream(info,"audio")["start_time"])
            self.assertAlmostEqual(difference,0.2,delta=0.07)

    def test_invalid_input_and_plan_do_not_write_outputs(self):
        plan=cv.convert(self.video,"1080p",plan_only=True)
        self.assertTrue(plan["copy_video"])
        self.assertTrue(plan["copy_audio"])
        self.assertFalse(list(self.root.glob("*.mp4")))
        bad=self.root/'bad.mov';bad.write_text('not video')
        with self.assertRaises(media.Error):cv.convert(bad)
        self.assertFalse(list(self.root.glob('.performance-*')))


if __name__ == '__main__': unittest.main()
