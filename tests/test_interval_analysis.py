"""Synthetic recording, saved-session, HTTP, and CLI interval regressions."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import wave

import numpy as np
import interval_analysis as app
import interval_sessions as sessions
import interval_takes as takes


REFERENCE = takes.RecordingKind.REFERENCE
COMPARISON = takes.RecordingKind.COMPARISON


def synthetic_take(frequency, vibrato_rate, depth, delay=0):
    rate = app.core.SAMPLE_RATE
    times = np.arange(int(2.8 * rate)) / rate
    hz = frequency * 2 ** (depth * np.sin(2 * np.pi * vibrato_rate * times) / 1200)
    phase = 2 * np.pi * np.cumsum(hz) / rate
    samples = sum(np.sin(n * phase) / n for n in range(1, 5)) * .22
    envelope = np.zeros_like(times)
    for start, end in ((.15, 1.15), (1.40, 2.65)):
        envelope = np.maximum(envelope, np.clip(np.minimum(times - start, end - times) / .03, 0, 1))
    samples *= envelope
    return np.concatenate((np.zeros(round(delay * rate)), samples)).astype(np.float32)


def wav_bytes(samples):
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(app.core.SAMPLE_RATE)
        wav.writeframes((samples * 32767).astype('<i2').tobytes())
    return output.getvalue()


class IntervalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.convert_video.configure_tools()
        cls.reference = wav_bytes(synthetic_take(220, 5.5, 40))
        cls.comparison = wav_bytes(synthetic_take(220 * 2 ** (7 / 12), 4.8, 60, .12))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sessions = self.root / 'sessions'
        self.files = [self.root / 'reference.wav', self.root / 'comparison.wav']
        for path, data in zip(self.files, (self.reference, self.comparison)):
            path.write_bytes(data)

    def server(self):
        server = app.IntervalServer(self.sessions)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 3)
        self.addCleanup(server.shutdown)
        return server

    def request(self, server, route, value=None, raw=None, mime='audio/wav', headers=None):
        headers = {'X-Editor-Token': server.token, **(headers or {})}
        data = None
        if value is not None:
            data = json.dumps(value).encode()
            headers['Content-Type'] = 'application/json'
        if raw is not None:
            data = raw
            headers['Content-Type'] = mime
        with urlopen(Request(server.origin + route, data=data, headers=headers), timeout=60) as response:
            payload = response.read()
            return payload if response.headers['Content-Type'] == 'audio/wav' else json.loads(payload)

    def upload_pair(self, server):
        self.request(server, '/upload/reference', raw=self.reference)
        self.request(server, '/upload/comparison', raw=self.comparison,
                     headers={'X-Playback-Delay': '0.10'})

    def decoded_pair(self):
        return {kind: takes.decode_take(path, kind, 'audio/wav', work=self.root)
                for kind, path in zip(takes.RecordingKind, self.files)}

    def assert_bad(self, server, route, value=None, **kwargs):
        with self.assertRaises(HTTPError) as caught:
            self.request(server, route, value=value, **kwargs)
        self.assertEqual(caught.exception.code, 400)
        self.assertIn('error', json.load(caught.exception))

    def test_upload_auth_validation_and_listening_derivative(self):
        server = self.server()
        with self.assertRaises(HTTPError) as caught:
            urlopen(server.origin + '/state')
        self.assertEqual(caught.exception.code, 403)
        take = self.request(server, '/upload/reference', raw=self.reference)['takes']['reference']
        self.assertAlmostEqual(take['duration'], 2.8, places=3)
        self.assertEqual(take['kind'], 'reference')
        payload = self.request(server, '/take/reference')
        with wave.open(io.BytesIO(payload)) as wav:
            self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 22050))
            self.assertAlmostEqual(wav.getnframes() / wav.getframerate(), take['duration'], places=3)
        self.assert_bad(server, '/upload/unknown', raw=self.reference)
        self.assert_bad(server, '/upload/comparison', raw=b'text', mime='text/plain')
        for delay in ('nan', '-1', '5.1'):
            self.assert_bad(server, '/upload/comparison', raw=self.comparison,
                            headers={'X-Playback-Delay': delay})
        self.assert_bad(server, '/analyze', {'alignment': 'auto'})
        self.assert_bad(server, '/save', {'title': 'Missing comparison', 'notes': ''})
        state = self.request(server, '/state')
        self.assertEqual(state['kinds'], [kind.value for kind in takes.RecordingKind])
        self.assertEqual(state['alignment_modes'], list(app.core.ALIGNMENT_MODES))
        self.assertEqual(state['limits']['bytes'], takes.RECORDING_LIMIT_BYTES)

    def test_analysis_reuses_pitch_tracks_until_the_owning_take_is_replaced(self):
        server = self.server()
        self.upload_pair(server)
        with patch.object(app.core, 'track_pitch', wraps=app.core.track_pitch) as track_pitch:
            automatic = self.request(server, '/analyze', {'alignment': 'auto'})
            self.assertEqual(track_pitch.call_count, 2)
            self.assertEqual(automatic, self.request(server, '/state'))
            self.assertEqual(automatic['analysis']['interval']['semitones'], 7)
            self.assertEqual(automatic['analysis']['alignment']['hint_seconds'], .1)
            manual = self.request(server, '/analyze', {'alignment': 'manual', 'offset_seconds': .12})
            self.assertEqual(track_pitch.call_count, 2)
            self.assertEqual(manual, self.request(server, '/state'))
            self.assertEqual(manual['analysis']['alignment']['mode'], 'manual')
            self.assertEqual(manual['analysis']['alignment']['offset_seconds'], .12)
            self.assert_bad(server, '/upload/reference', raw=b'not audio')
            self.assertEqual(self.request(server, '/state'), manual)
            self.request(server, '/analyze', {'alignment': 'auto'})
            self.assertEqual(track_pitch.call_count, 2)
            replaced = self.request(server, '/upload/reference', raw=self.reference)
            self.assertEqual(replaced, self.request(server, '/state'))
            self.assertIsNone(replaced['analysis'])
            self.assertEqual(replaced['takes']['comparison'], automatic['takes']['comparison'])
            result = self.request(server, '/analyze', {'alignment': 'auto'})['analysis']
            self.assertEqual(track_pitch.call_count, 3)
            self.assertEqual(result, automatic['analysis'])

    def test_deep_session_json_is_isolated_and_load_returns_bad_request(self):
        server = self.server()
        self.upload_pair(server)
        good = server.store.save('Good sibling', '', server.takes, None)
        corrupt = self.sessions / '20261013-010000-deep'
        corrupt.mkdir()
        (corrupt / 'session.json').write_bytes(b'[' * 20000 + b'0' + b']' * 20000)
        before = self.request(server, '/state')
        entries = {entry['id']: entry for entry in before['sessions']}
        self.assertNotIn('error', entries[good.id])
        self.assertIn('Invalid session JSON', entries[corrupt.name]['error'])
        self.assertLess(len(entries[corrupt.name]['error']), 500)
        with self.assertRaises(HTTPError) as caught:
            self.request(server, '/load', {'id': corrupt.name})
        with caught.exception as response:
            self.assertEqual(response.code, 400)
            self.assertIn('Invalid session JSON', json.load(response)['error'])
        self.assertEqual(self.request(server, '/state'), before)
        self.assertEqual(self.request(server, '/load', {'id': good.id})['loaded_session']['id'], good.id)

    def test_analysis_save_reopen_and_note_updates(self):
        server = self.server()
        self.upload_pair(server)
        result = self.request(server, '/analyze', {'alignment': 'auto'})['analysis']
        self.assertEqual(result['interval']['semitones'], 7)
        self.assertEqual(result['alignment']['mode'], 'auto')
        self.assertAlmostEqual(result['alignment']['offset_seconds'], .12, delta=.03)
        result = self.request(server, '/analyze', {'alignment': 'manual', 'offset_seconds': .12})['analysis']
        self.assertEqual(result['alignment']['mode'], 'manual')
        saved = self.request(server, '/save', {'title': 'A fifth', 'notes': 'Headphones; first chorus.'})
        loaded = saved['loaded_session']
        self.assertEqual(loaded['notes'], 'Headphones; first chorus.')
        folder = self.sessions / loaded['id']
        self.assertEqual(set(p.name for p in folder.iterdir()), {'reference.wav', 'comparison.wav', 'session.json'})
        wire = json.loads((folder / 'session.json').read_text())
        self.assertEqual(sessions.IntervalSession.parse(wire, loaded['id']).wire(), wire)
        self.assertEqual((folder / 'reference.wav').read_bytes(), self.reference)
        self.assertEqual((folder / 'comparison.wav').read_bytes(), self.comparison)
        fresh = self.server()
        self.assertEqual(self.request(fresh, '/state')['sessions'][0]['id'], loaded['id'])
        restored = self.request(fresh, '/load', {'id': loaded['id']})
        self.assertEqual(restored['loaded_session'], loaded)
        self.assertEqual(restored['analysis'], result)
        self.assertEqual(restored['takes']['comparison']['playback_delay'], .10)
        self.assertEqual(self.request(fresh, '/take/reference'), self.request(server, '/take/reference'))
        updated = self.request(fresh, '/update', {**loaded, 'title': 'Renamed'})
        self.assertEqual(updated['loaded_session'], {**loaded, 'title': 'Renamed'})
        self.assertEqual(sessions.SessionStore(self.sessions).load(loaded['id']).notes, loaded['notes'])
        with patch.object(app.media, 'run') as reveal:
            self.assertEqual(self.request(fresh, '/reveal', {'id': loaded['id']}), {'revealed': True})
            self.assertEqual(reveal.call_args.args[0], ['/usr/bin/open', '-R', folder])
        self.assert_bad(fresh, '/update', {'id': '19990101-000000-other', 'title': 'x', 'notes': ''})
        self.request(fresh, '/upload/reference', raw=self.reference)
        state = self.request(fresh, '/state')
        self.assertIsNone(state['analysis'])
        self.assertIsNone(state['loaded_session'])

    def test_choose_uses_decoder_and_cancel_preserves_state(self):
        server = self.server()
        with patch.object(app, 'choose_file', return_value=self.files[0]):
            state = self.request(server, '/choose', {'kind': 'reference', 'audio_track': 0})
        self.assertEqual(state['takes']['reference']['playback_delay'], 0)
        self.assertEqual(state['takes']['reference']['audio_track'], 0)
        self.assertTrue(self.request(server, '/take/reference').startswith(b'RIFF'))
        with patch.object(app, 'choose_file', return_value=None):
            self.assertEqual(self.request(server, '/choose', {'kind': 'reference'}), state)
        self.assert_bad(server, '/choose', {'kind': 'reference', 'audio_track': True})

    def test_bad_sessions_are_isolated_and_traversal_is_rejected(self):
        store = sessions.SessionStore(self.sessions)
        pair = self.decoded_pair()
        saved = store.save('Good', '', pair, None)
        corrupt = self.sessions / '20261013-010000-corrupt'
        corrupt.mkdir()
        (corrupt / 'session.json').write_text('{not json')
        link = self.sessions / '20261013-010000-link'
        link.symlink_to(self.sessions / saved.id, target_is_directory=True)
        recording = self.sessions / saved.id / 'reference.wav'
        recording.unlink()
        recording.symlink_to(self.files[0])
        server = self.server()
        entries = {entry['id']: entry for entry in self.request(server, '/state')['sessions']}
        self.assertEqual(set(entries), {saved.id, corrupt.name, link.name})
        self.assertTrue(all('error' in entry for entry in entries.values()))
        for id in (saved.id, link.name, corrupt.name, '../x', '/tmp/x', '..\\x'):
            self.assert_bad(server, '/load', {'id': id})
        self.assert_bad(server, '/reveal', {'id': '../x'})
        self.assertIsNone(self.request(server, '/state')['loaded_session'])

    def test_no_follow_root_json_recording_and_non_regular_files(self):
        pair = self.decoded_pair()
        store = sessions.SessionStore(self.sessions)
        saved = store.save('Safe', '', pair, None)
        root_link = self.root / 'linked-root'
        root_link.symlink_to(self.sessions, target_is_directory=True)
        with self.assertRaises(OSError):
            sessions.SessionStore(root_link)
        manifest = self.sessions / saved.id / 'session.json'
        original = self.root / 'metadata.json'
        manifest.rename(original)
        manifest.symlink_to(original)
        with self.assertRaises(OSError):
            store.load(saved.id)
        manifest.unlink()
        original.rename(manifest)
        recording = self.sessions / saved.id / 'comparison.wav'
        recording.unlink()
        os.mkfifo(recording)
        with self.assertRaises(app.media.Error):
            store.open_recording(saved.id, COMPARISON)
        self.assertIn('error', store.list()[0])

    def test_open_recording_keeps_validated_descriptor_after_path_swap(self):
        store = sessions.SessionStore(self.sessions)
        saved = store.save('Descriptor', '', self.decoded_pair(), None)
        source, record = store.open_recording(saved.id, REFERENCE)
        with source:
            path = self.sessions / saved.id / record.file
            path.rename(path.with_suffix('.old'))
            path.symlink_to(self.files[1])
            self.assertEqual(source.read(), self.reference)
        with self.assertRaises(OSError):
            store.open_recording(saved.id, REFERENCE)

    def test_collision_and_metadata_is_published_last(self):
        store = sessions.SessionStore(self.sessions)
        pair = self.decoded_pair()
        with patch.object(sessions, 'datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 13, tzinfo=timezone.utc)
            first = store.save('x' * 120, 'preserve', pair, None)
            second = store.save('x' * 120, 'preserve', pair, None)
        self.assertNotEqual(first.id, second.id)
        self.assertTrue(second.id.endswith('-2'))
        self.assertRegex(second.id, sessions.ID_PATTERN)
        with patch.object(sessions.conversion_storage, 'write_json', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                store.save('Incomplete', '', pair, None)
        failures = [entry for entry in store.list() if 'error' in entry]
        self.assertEqual(len(failures), 1)
        self.assertFalse((self.sessions / failures[0]['id'] / 'session.json').exists())
        self.assertEqual(store.load(first.id).notes, 'preserve')

    def test_record_validation_rejects_invalid_wire_records(self):
        record = self.decoded_pair()[REFERENCE].recording.wire()
        self.assertEqual(takes.Recording.parse(record).wire(), record)
        bad = {'kind': 'other', 'file': '../outside.wav', 'mime': 'text/plain',
               'duration': float('nan'), 'size_bytes': True, 'playback_delay': 1,
               'audio_track': -1}
        for key, value in bad.items():
            with self.subTest(key=key), self.assertRaises(app.media.Error):
                takes.Recording.parse({**record, key: value})
        for name in ('', '.', '..', 'a\\b', 'a\x00b'):
            with self.assertRaises(app.media.Error):
                takes.Recording.parse({**record, 'file': name})
        self.assertEqual(takes.recording_extension('audio/webm;codecs=opus'), 'webm')
        self.assertEqual(takes.recording_extension('audio/custom'), 'bin')

    def test_busy_during_decode_analysis_and_save_and_cleanup(self):
        server = app.IntervalServer(self.sessions)
        self.addCleanup(server.server_close)
        work = server.work
        entered, release = threading.Event(), threading.Event()
        real_decode = takes.decode_take
        def decode(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return real_decode(*args, **kwargs)
        with patch.object(takes, 'decode_take', side_effect=decode):
            worker = threading.Thread(target=server.receive_upload,
                                      args=('/upload/reference', self.reference, {'Content-Type': 'audio/wav'}))
            worker.start()
            self.assertTrue(entered.wait(2))
            self.assertTrue(server.busy())
            release.set()
            worker.join(10)
            self.assertFalse(worker.is_alive())
        self.assertFalse(server.busy())
        server.receive_upload('/upload/comparison', self.comparison, {'Content-Type': 'audio/wav'})
        def analyze(*args, **kwargs):
            self.assertTrue(server.busy())
            return {'test': True}
        with patch.object(app.core, 'compare_tracks', side_effect=analyze):
            server.dispatch('/analyze', {'alignment': 'auto'})
        def save(*args):
            self.assertTrue(server.busy())
            raise app.media.Error('save test')
        with patch.object(server.store, 'save', side_effect=save):
            with self.assertRaisesRegex(app.media.Error, 'save test'):
                server.dispatch('/save', {'title': 'x', 'notes': ''})
        self.assertFalse(server.busy())
        self.assertEqual(server.dispatch('/close', {}), {'closed': True})
        with self.assertRaisesRegex(app.media.Error, 'closed'):
            server.dispatch('/load', {'id': '20261013-000000-x'})
        server.server_close()
        self.assertFalse(work.exists())

    def test_cli_json_and_save_with_manual_offset(self):
        output = io.StringIO()
        args = [*(str(p) for p in self.files), '--json', '--sessions-dir', str(self.sessions)]
        with redirect_stdout(output):
            self.assertEqual(app.main(args), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result['interval']['semitones'], 7)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(app.main(args + ['--offset', '.12', '--save', '--title', 'x', '--notes', 'CLI note']), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result['alignment']['mode'], 'manual')
        store = sessions.SessionStore(self.sessions)
        self.assertEqual(len(store.list()), 1)
        saved = store.load(store.list()[0]['id'])
        self.assertEqual((saved.title, saved.notes), ('x', 'CLI note'))
        self.assertEqual(saved.analysis.wire(), result)

    def test_cli_save_captures_original_audio_before_external_source_changes(self):
        replacement = wav_bytes(synthetic_take(880, 5.5, 40))
        self.assertEqual(len(replacement), len(self.reference))
        original_analyze = app.core.analyze_pair
        def change_original(*args, **kwargs):
            self.files[0].write_bytes(replacement)
            return original_analyze(*args, **kwargs)
        output = io.StringIO()
        arguments = [*(str(path) for path in self.files), '--save', '--json',
                     '--sessions-dir', str(self.sessions)]
        with patch.object(app.core, 'analyze_pair', side_effect=change_original), redirect_stdout(output):
            self.assertEqual(app.main(arguments), 0)
        store = sessions.SessionStore(self.sessions)
        saved = store.load(store.list()[0]['id'])
        self.assertEqual(saved.analysis.wire(), json.loads(output.getvalue()))
        self.assertEqual(saved.analysis.interval['semitones'], 7)
        self.assertEqual(self.files[0].read_bytes(), replacement)
        decoded = []
        for kind, expected in ((REFERENCE, self.reference), (COMPARISON, self.comparison)):
            path = self.sessions / saved.id / saved.recordings[kind].file
            self.assertEqual(path.read_bytes(), expected)
            decoded.append(takes.decode_take(path, kind, 'audio/wav', work=self.root).samples)
        self.assertEqual(original_analyze(*decoded)['interval']['semitones'], 7)

    def test_old_take_disposal_failure_cannot_rollback_committed_replacement(self):
        store = sessions.SessionStore(self.sessions)
        saved = store.save('Load replacement', 'retained notes', self.decoded_pair(), None)
        real_unlink = Path.unlink
        for route in ('upload', 'choose', 'load'):
            for failed_file in ('path', 'listening'):
                with self.subTest(route=route, failed_file=failed_file), app.IntervalServer(self.sessions) as server:
                    server.receive_upload('/upload/reference', self.reference, {'Content-Type': 'audio/wav'})
                    old = server.takes[REFERENCE]
                    failing = getattr(old, failed_file)
                    def unlink(path, *args, **kwargs):
                        if path == failing:
                            raise OSError('old take disposal failed')
                        return real_unlink(path, *args, **kwargs)
                    with patch.object(Path, 'unlink', new=unlink):
                        if route == 'upload':
                            response = server.receive_upload('/upload/reference', self.comparison,
                                                             {'Content-Type': 'audio/wav'})
                        elif route == 'choose':
                            with patch.object(app, 'choose_file', return_value=self.files[1]):
                                response = server.dispatch('/choose', {'kind': 'reference'})
                        else:
                            response = server.dispatch('/load', {'id': saved.id})
                    current = server.takes[REFERENCE]
                    self.assertIsNot(current, old)
                    expected = self.reference if route == 'load' else self.comparison
                    self.assertEqual(current.path.read_bytes(), expected)
                    self.assertTrue(current.listening.exists())
                    self.assertEqual(server.get_resource('/take/reference')[0], current.listening.read_bytes())
                    state = server.snapshot()
                    self.assertEqual(state['takes']['reference'], current.recording.wire())
                    self.assertEqual(response, state)
                    if route == 'upload':
                        self.assertIsNone(state['loaded_session'])
                    if route == 'load':
                        self.assertEqual(state['loaded_session']['id'], saved.id)
                        self.assertTrue(all(take.path.exists() and take.listening.exists()
                                            for take in server.takes.values()))
                    self.assertEqual({path for path in (old.path, old.listening) if path.exists()}, {failing})
                    self.assertFalse(server.busy())

    def test_failed_session_staging_preserves_current_takes_and_cleans_staged_files(self):
        store = sessions.SessionStore(self.sessions)
        saved = store.save('Cannot decode', '', self.decoded_pair(), None)
        with app.IntervalServer(self.sessions) as server:
            server.dispatch('/load', {'id': saved.id})
            before, files = server.snapshot(), set(server.work.iterdir())
            original_decode = takes.decode_take
            def fail_comparison(source, kind, *args, **kwargs):
                if kind == COMPARISON:
                    raise app.media.Error('comparison decode failed')
                return original_decode(source, kind, *args, **kwargs)
            with patch.object(takes, 'decode_take', side_effect=fail_comparison):
                with self.assertRaisesRegex(app.media.Error, 'comparison decode failed'):
                    server.dispatch('/load', {'id': saved.id})
            self.assertEqual(server.snapshot(), before)
            self.assertEqual(set(server.work.iterdir()), files)

    def test_session_listing_uses_instants_and_keeps_equal_dates_and_errors_stable(self):
        store = sessions.SessionStore(self.sessions)
        pair = self.decoded_pair()
        records = [store.save(title, '', pair, None) for title in ('earlier', 'later', 'same instant')]
        dates = ('2026-10-13T10:00:00+02:00', '2026-10-13T09:30:00+00:00',
                 '2026-10-13T05:30:00-04:00')
        for record, created in zip(records, dates):
            sessions.conversion_storage.write_json(self.sessions / record.id / 'session.json',
                                                    {**record.wire(), 'created': created})
        broken = ['20261013-235959-z-broken', '20261013-235959-a-broken']
        for name in broken:
            (self.sessions / name).mkdir()
        earlier, later, same = (record.id for record in records)
        directory_order = [earlier, broken[0], later, broken[1], same]
        with patch.object(sessions.os, 'listdir', return_value=directory_order):
            result = store.list()
        self.assertEqual([entry['id'] for entry in result], [later, same, earlier, *broken])
        self.assertTrue(all('error' not in entry for entry in result[:3]))
        self.assertTrue(all('error' in entry for entry in result[3:]))

    def test_capture_limits_and_failed_copy_leave_no_partial_take(self):
        for source in (io.BytesIO(b'12345'), io.BytesIO(b'')):
            with self.subTest(source=source.getvalue()), patch.object(takes, 'RECORDING_LIMIT_BYTES', 4):
                with self.assertRaises(app.media.Error):
                    takes.capture_take(source, REFERENCE, 'audio/wav', work=self.root)
            self.assertEqual(set(self.root.iterdir()), set(self.files))
        class InterruptedSource:
            def read(self, size):
                raise OSError('source read failed')
        with self.assertRaisesRegex(OSError, 'source read failed'):
            takes.capture_take(InterruptedSource(), REFERENCE, 'audio/wav', work=self.root)
        self.assertEqual(set(self.root.iterdir()), set(self.files))

    def test_cli_rejects_invalid_arguments_without_starting_server(self):
        for arguments in ([str(self.files[0])], ['--offset', 'nan'], ['--offset', '6'], ['--audio-track', '-1']):
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()):
                self.assertEqual(app.main(arguments), 1)
        with patch.object(app, 'serve') as serve:
            self.assertEqual(app.main(['--no-open', '--sessions-dir', str(self.sessions)]), 0)
            self.assertEqual(serve.call_args.args[0].sessions_dir, self.sessions)
            self.assertTrue(serve.call_args.args[0].no_open)

    def test_failed_decode_preserves_take_and_cleans_temporary_files(self):
        with app.IntervalServer(self.sessions) as server:
            server.receive_upload('/upload/reference', self.reference, {'Content-Type': 'audio/wav'})
            before = server.snapshot()
            files = set(server.work.iterdir())
            with patch.object(takes.bpm, 'decode', return_value=(np.array([float('nan')]), {})):
                with self.assertRaisesRegex(app.media.Error, 'finite'):
                    server.receive_upload('/upload/reference', self.reference, {'Content-Type': 'audio/wav'})
            self.assertEqual(server.snapshot(), before)
            self.assertEqual(set(server.work.iterdir()), files)
            self.assertFalse(server.busy())

    def test_root_replacement_and_invalid_session_metadata_are_isolated(self):
        store = sessions.SessionStore(self.sessions)
        saved = store.save('Validation', '', self.decoded_pair(), None)
        wire = saved.wire()
        for key, value in (('title', 'x' * 121), ('notes', 'x' * 5001),
                           ('created', '2026-10-13T00:00:00'), ('analysis', {'version': 1})):
            with self.subTest(key=key), self.assertRaises(app.media.Error):
                sessions.IntervalSession.parse({**wire, key: value}, saved.id)
        for number in (10 ** 400, -10 ** 400, True, float('inf')):
            with self.assertRaises(app.media.Error):
                takes.finite_number(number, 'Offset', -5, 5)
        moved = self.root / 'moved-sessions'
        self.sessions.rename(moved)
        self.sessions.symlink_to(moved, target_is_directory=True)
        self.assertIn('error', store.list()[0])
        with self.assertRaises(OSError):
            store.load(saved.id)

    def test_background_forwards_options_without_background_recursion(self):
        with patch.object(app, 'BUILD', self.root), patch.object(app.subprocess, 'Popen') as launch:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(app.main(['--background', '--no-open', '--sessions-dir', str(self.sessions),
                                           '--title', '-leading title', '--notes', 'notes', '--offset', '.1']), 0)
        command = launch.call_args.args[0]
        self.assertNotIn('--background', command)
        self.assertIn('--no-open', command)
        self.assertIn('--title=-leading title', command)
        self.assertIn('--offset=0.1', command)
        self.assertIn(str(self.sessions), command)
        self.assertTrue(launch.call_args.kwargs['start_new_session'])
        self.assertEqual(len(list((self.root / 'interval-analysis-jobs').glob('*.log'))), 1)


if __name__ == '__main__':
    unittest.main()
