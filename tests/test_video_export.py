"""Behavioral checks for resizing/remuxing without changing audio or timing."""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

import convert_video as cv
import performance_audio as media
import video_export as export
from conversion_settings import ConversionSettings, VideoSettings
import performance_audio_effects as effects


class PreserveExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cv.configure_tools()
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)/'base.mov'
        cls.ff('-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=60:duration=1',
               '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=1',
               '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac',
               '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', cls.base)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    @staticmethod
    def ff(*args):
        return media.run([media.tool('ffmpeg'), '-v', 'error', '-nostdin', '-y', *args])

    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)

    def packets(self, path, selector):
        data = json.loads(media.run([media.tool('ffprobe'), '-v', 'error', '-select_streams', selector,
            '-show_packets', '-show_entries', 'packet=data_hash', '-show_data_hash', 'sha256', '-of', 'json', path]))
        return [p['data_hash'] for p in data['packets']]

    def test_resize_keeps_sixty_fps_color_tags_and_audio_packets(self):
        original = media.fingerprint(self.base)
        result = export.export(self.base, VideoSettings(mode='resize', resolution='540p', container='mov'), self.root)
        track = media.stream(media.probe(result), 'video')
        self.assertEqual(result.suffix, '.mov')
        self.assertEqual((track['width'], track['height']), (960, 540))
        self.assertEqual(cv.ratio(track['avg_frame_rate']), 60)
        original_track = media.stream(media.probe(self.base), 'video')
        for field in export.COLOR_FIELDS:
            self.assertEqual(track.get(field), original_track.get(field))
        self.assertEqual(self.packets(self.base, 'a:0'), self.packets(result, 'a:0'))
        self.assertEqual(media.fingerprint(self.base), original)

    def test_extension_only_copies_both_streams_and_numbers_outputs(self):
        settings = VideoSettings(container='mp4')
        result = export.export(self.base, settings, self.root)
        self.assertEqual(result.suffix, '.mp4')
        for kind in ('a:0', 'v:0'): self.assertEqual(self.packets(self.base, kind), self.packets(result, kind))
        self.assertNotEqual(export.export(self.base, settings, self.root), result)

    def test_variable_frame_timing_and_audio_offset_survive_resize(self):
        source = self.root/'vfr.mov'
        self.ff('-itsoffset', '0.2', '-i', self.base, '-i', self.base, '-map', '0:v', '-map', '1:a',
                '-vf', "select='not(mod(n,2))+not(mod(n,5))'", '-fps_mode', 'vfr',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'copy', source)
        result = export.export(source, VideoSettings(mode='resize', resolution='540p'), self.root)
        export.validate_frame_timing(source, result)
        self.assertEqual(self.packets(source, 'a:0'), self.packets(result, 'a:0'))

    def test_hlg_ten_bit_remains_hlg_ten_bit(self):
        source = self.root/'hlg.mov'
        self.ff('-i', self.base, '-an', '-c:v', 'libx265', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p10le',
                '-x265-params', 'log-level=error:pools=1:colorprim=9:transfer=18:colormatrix=9', source)
        result = export.export(source, VideoSettings(mode='resize', resolution='540p'), self.root)
        track = media.stream(media.probe(result), 'video')
        self.assertEqual(track['color_transfer'], 'arib-std-b67')
        self.assertEqual(track['color_primaries'], 'bt2020')
        self.assertEqual(track['pix_fmt'], 'yuv420p10le')

    def test_dolby_vision_mov_is_rejected_before_processing(self):
        info = media.probe(self.base)
        media.stream(info, 'video')['side_data_list'] = [{'side_data_type': 'DOVI configuration record', 'dv_profile': 8}]
        with self.assertRaisesRegex(media.Error, 'Choose MP4'):
            export.plan_export(info, VideoSettings(mode='resize', resolution='540p', container='mov'))

    def test_explicit_mp4_keeps_pcm_or_fails_without_publishing(self):
        source = self.root/'pcm.mov'
        self.ff('-i', self.base, '-c:v', 'copy', '-c:a', 'pcm_s24le', source)
        try:
            result = export.export(source, VideoSettings(container='mp4'), self.root)
        except media.Error as error:
            self.assertRegex(str(error), 'not currently supported|Could not find tag|Could not write header')
            self.assertFalse(list(self.root.glob('pcm-original*.mp4')))
            self.assertFalse(list(self.root.glob('.performance-*')))
        else:
            audio = media.stream(media.probe(result), 'audio')
            self.assertEqual(audio['codec_name'], 'pcm_s24le')
            self.assertEqual(self.packets(source, 'a:0'), self.packets(result, 'a:0'))

    def test_old_presets_load_and_resize_does_not_force_aac(self):
        old = asdict(VideoSettings()); old.pop('container')
        self.assertEqual(VideoSettings.parse(old).container, 'auto')
        value = {'audio': effects.load_presets()['presets']['clean'], 'video': asdict(VideoSettings(mode='resize', resolution='720p')),
                 'audio_enabled': False, 'share': False}
        self.assertFalse(ConversionSettings.parse(value).share)
        value['video']['container'] = 'not-a-container'
        with self.assertRaises(media.Error): ConversionSettings.parse(value)


if __name__ == '__main__': unittest.main()
