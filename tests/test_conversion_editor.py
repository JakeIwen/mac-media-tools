"""Run with PYTHONPATH=macbook/scripts; exercise public editor and job entry points."""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import conversion_editor as editor
from conversion_jobs import ConversionJobs
from conversion_settings import ConversionSettings, VideoSettings


class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        editor.convert_video.configure_tools()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = editor.effects.DEFAULT_CONFIG
        self.preset = editor.effects.load_presets(self.config)['presets']['jake-preferred']
        self.value = {'audio': self.preset, 'video': asdict(VideoSettings()), 'audio_enabled': True, 'share': False}

    def test_validation_and_immutable_saved_presets(self):
        jobs = ConversionJobs([], self.config, self.root)
        before = self.config.read_bytes()
        settings = ConversionSettings.parse(self.value)
        first, second = jobs.save(settings), jobs.save(settings)
        self.assertNotEqual(first, second)
        self.assertEqual(jobs.presets()[first]['audio']['ambience_mix'], .168)
        self.assertEqual(self.config.read_bytes(), before)
        self.value['video']['resolution'] = '720p'
        with self.assertRaises(editor.media.Error): ConversionSettings.parse(self.value)
        self.value['video']['mode'] = 'sdr'
        ConversionSettings.parse(self.value)
        self.value['audio']['reverb_mix'] = float('nan')
        with self.assertRaises(editor.media.Error): ConversionSettings.parse(self.value)

    def test_loopback_token_origin_and_bounds(self):
        jobs = ConversionJobs([], self.config, self.root)
        server = editor.EditorServer(jobs)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with self.assertRaises(HTTPError) as caught: urlopen(server.origin+'/state')
        self.assertEqual(caught.exception.code, 403)
        headers = {'X-Editor-Token': server.token, 'Content-Type': 'application/json'}
        with urlopen(Request(server.origin+'/state', headers=headers)) as response:
            self.assertIn('jake-preferred', json.load(response)['presets'])
        request = Request(server.origin+'/save', data=json.dumps(self.value).encode(),
                          headers={**headers, 'Origin': 'https://foreign.invalid'})
        with self.assertRaises(HTTPError): urlopen(request)
        self.assertFalse(list(self.root.glob('preset-*.json')))
        with urlopen(Request(server.origin+'/save', data=json.dumps(self.value).encode(), headers=headers)) as response:
            self.assertIn(json.load(response)['id'], jobs.presets())

    def test_none_preserves_video_and_adjustment_reencodes(self):
        source = self.root/'test.mov'
        editor.media.run([editor.media.tool('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i',
            'testsrc2=size=128x96:rate=30:duration=2', '-f', 'lavfi', '-i',
            'sine=frequency=440:sample_rate=48000:duration=2', '-c:v', 'libx264', '-c:a', 'aac', source])
        before = source.read_bytes()
        jobs = ConversionJobs([source], self.config, self.root/'jobs', self.root)
        job = self.root/'work'; job.mkdir()
        config = job/'audio.json'
        config.write_text(json.dumps({'default': 'custom', 'presets': {'custom': self.preset}}))
        output = jobs.convert_one(source, ConversionSettings.parse(self.value), job, config)
        def packets(path):
            value = json.loads(editor.media.run([editor.media.tool('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
                '-show_packets', '-show_entries', 'packet=data_hash', '-show_data_hash', 'sha256', '-of', 'json', path]))
            return [p['data_hash'] for p in value['packets']]
        self.assertEqual(packets(source), packets(output))
        self.assertEqual(editor.media.stream(editor.media.probe(output), 'audio')['codec_name'], 'pcm_s24le')
        self.value['video'].update(mode='adjust', brightness=-.1)
        changed = jobs.convert_one(source, ConversionSettings.parse(self.value), job, config)
        self.assertNotEqual(packets(source), packets(changed))
        self.assertEqual(source.read_bytes(), before)
        self.assertTrue(changed.with_name(changed.name+'.conversion.json').exists())


if __name__ == '__main__': unittest.main()
