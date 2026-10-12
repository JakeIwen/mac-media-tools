"""Run with PYTHONPATH=macbook/scripts; exercise public editor and job entry points."""
from dataclasses import asdict
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
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
        self.assertEqual(jobs.presets()[0][first]['audio']['ambience_mix'], .168)
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
            self.assertIn(json.load(response)['id'], jobs.presets()[0])

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
        output = jobs.convert_one(source, ConversionSettings.parse(self.value), job, config).path
        def packets(path):
            value = json.loads(editor.media.run([editor.media.tool('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
                '-show_packets', '-show_entries', 'packet=data_hash', '-show_data_hash', 'sha256', '-of', 'json', path]))
            return [p['data_hash'] for p in value['packets']]
        self.assertEqual(packets(source), packets(output))
        self.assertEqual(editor.media.stream(editor.media.probe(output), 'audio')['codec_name'], 'pcm_s24le')
        self.value['video'].update(mode='adjust', brightness=-.1)
        changed = jobs.convert_one(source, ConversionSettings.parse(self.value), job, config).path
        self.assertNotEqual(packets(source), packets(changed))
        self.assertEqual(source.read_bytes(), before)
        self.assertTrue(changed.with_name(changed.name+'.conversion.json').exists())

    def test_recipe_failure_keeps_published_output_authoritative(self):
        from conversion_jobs import ConversionResult
        result = self.root/'finished.mp4'
        result.write_bytes(b'published')
        outcome = ConversionResult(result)
        with patch('conversion_jobs.write_json', side_effect=OSError('disk full')):
            ConversionJobs.save_recipe(self.root/'source.mov', ConversionSettings.parse(self.value), result, outcome)
        self.assertEqual(outcome.path.read_bytes(), b'published')
        self.assertIn('Video saved', outcome.warnings[0])

    def test_closed_session_cannot_start_and_stale_selection_is_rejected(self):
        jobs = ConversionJobs([], self.config, self.root)
        with editor.EditorServer(jobs) as server:
            with self.assertRaisesRegex(editor.media.Error, 'selection changed'):
                server.dispatch('/convert', {'settings': self.value, 'selection_revision': -1})
            server.dispatch('/close', {})
            with self.assertRaisesRegex(editor.media.Error, 'session is closed'):
                server.dispatch('/convert', {'settings': self.value, 'selection_revision': 0})

    def test_close_is_serialized_with_start(self):
        jobs = ConversionJobs([], self.config, self.root)
        entered, release = threading.Event(), threading.Event()
        started = []
        def start(settings):
            entered.set()
            self.assertTrue(release.wait(2))
            started.append(True)
        with editor.EditorServer(jobs) as server, patch.object(jobs, 'start', start), patch.object(jobs, 'is_running', lambda: bool(started)):
            process = threading.Thread(target=lambda: server.dispatch('/convert', {'settings': self.value, 'selection_revision': 0}))
            process.start()
            self.assertTrue(entered.wait(2))
            errors = []
            def close():
                try: server.dispatch('/close', {})
                except editor.media.Error as error: errors.append(str(error))
            closer = threading.Thread(target=close)
            closer.start()
            release.set()
            process.join(2); closer.join(2)
            self.assertFalse(server.closing)
            self.assertEqual(len(errors), 1)

    def test_tool_launches_confirmation_instead_of_processing(self):
        import argparse
        args = argparse.Namespace(videos=[Path('/tmp/selected.mov')], preset='jake-preferred',
                                  finder_selection=True, background=True, share=False, audio_track=0)
        with patch.object(editor, 'main') as launch:
            editor.launch_for_tool('audio', args)
        self.assertIn('--mode', launch.call_args.args[0])
        self.assertIn('audio', launch.call_args.args[0])
        self.assertIn('--background', launch.call_args.args[0])
        self.assertIn('/tmp/selected.mov', launch.call_args.args[0])

    def test_selection_origin_distinguishes_finder_picker_and_paths(self):
        from media_selection import select_media, SelectionOrigin
        with patch('media_selection.media.run', return_value='{"paths":["/tmp/a.mov"],"origin":"Finder selection"}'):
            self.assertEqual(select_media(True).origin, SelectionOrigin.FINDER)
        with patch('media_selection.media.run', return_value='{"paths":["/tmp/a.mov"],"origin":"File picker"}'):
            self.assertEqual(select_media(True).origin, SelectionOrigin.PICKER)
        selection = select_media(paths=[Path('/tmp/a.mov')])
        self.assertEqual(selection.origin, SelectionOrigin.ARGUMENTS)
        self.assertEqual(selection.files, (Path('/tmp/a.mov').resolve(),))

    def test_cancellation_after_publication_keeps_durable_output_record(self):
        script = '''import os, signal, sys
from pathlib import Path
import convert_video, performance_audio as media
convert_video.configure_tools()
root=Path(sys.argv[1])
def published(path):
    os.kill(os.getpid(), signal.SIGINT)
    (root/'record.txt').write_text(str(path))
try:
    media.publish(['-f','lavfi','-i','color=size=64x64:rate=30:duration=0.2','-c:v','libx264'],root/'output.mp4',on_published=published)
except KeyboardInterrupt:
    sys.exit(130)
'''
        result = subprocess.run([sys.executable, '-c', script, str(self.root)], capture_output=True)
        self.assertEqual(result.returncode, 130, result.stderr)
        self.assertEqual(Path((self.root/'record.txt').read_text()), self.root/'output.mp4')
        self.assertTrue((self.root/'output.mp4').is_file())

    def test_selection_metadata_and_signature_must_be_same_version(self):
        from media_selection import MediaSelection, SelectionOrigin
        selected = MediaSelection((self.root/'take.mov',), SelectionOrigin.PICKER)
        info = {'streams': [{'codec_type': 'video', 'duration': '2'}]}
        with patch('media_selection.media.probe', return_value=info), patch('media_selection.media.fingerprint', side_effect=[{'size': 1, 'mtime_ns': 1}, {'size': 2, 'mtime_ns': 2}]):
            with self.assertRaisesRegex(editor.media.Error, 'changed while reading'):
                selected.inspect()

    def test_failed_selection_inspection_does_not_mutate_session(self):
        jobs = ConversionJobs([], self.config, self.root)
        with editor.EditorServer(jobs) as server, patch.object(editor, 'select_media') as selection:
            selection.return_value.inspect.side_effect = OSError('file disappeared')
            with self.assertRaises(OSError): server.dispatch('/choose', {})
            self.assertEqual(server.snapshot()['selection_revision'], 0)
            self.assertEqual(server.snapshot()['files'], [])
            self.assertEqual(jobs.files, [])

    def test_conversion_error_does_not_hide_cancellation_control_error(self):
        from dataclasses import replace
        from media_task_control import TaskStatus, TaskState
        from conversion_storage import write_json
        from conversion_jobs import ConversionProgress
        jobs = ConversionJobs([self.root/'take.mov'], self.config, self.root/'jobs')
        with patch('conversion_jobs.MediaTask') as task:
            jobs.start(ConversionSettings.parse(self.value))
            task.return_value.snapshot.return_value = replace(TaskStatus.idle(), state=TaskState.CANCELLING,
                running=True, errors=('cleanup could not be confirmed',))
            write_json(jobs.job/'progress.json', asdict(ConversionProgress(errors=['earlier conversion failed'])))
            errors = jobs.status()['task']['errors']
            self.assertEqual(errors, ['cleanup could not be confirmed', 'earlier conversion failed'])


if __name__ == '__main__': unittest.main()
