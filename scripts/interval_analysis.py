#!/usr/bin/env python3
"""Compare singing intervals in a short-lived local recorder or two media files."""
import argparse
from contextlib import contextmanager, suppress
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
import convert_video
import interval_core as core
import performance_audio as media
from interval_sessions import SessionStore, SESSIONS_DIRNAME, TITLE_LIMIT, NOTES_LIMIT
from interval_takes import (RecordingKind, RECORDING_LIMIT_BYTES, RECORDING_LIMIT_SECONDS,
                            capture_take, file_mime, finite_number, media_type)
from loopback_session import LoopbackServer

DIRECTORY = Path(__file__).resolve().parent
APP = next((p for p in DIRECTORY.parents if p.suffix == '.app'), None)
BUILD = APP.parent if APP else DIRECTORY.parent / 'build'


def choose_file():
    script = '''const app=Application.currentApplication();app.includeStandardAdditions=true;
let path=null;try{path=app.chooseFile({withPrompt:'Choose an audio or video take',
ofType:['public.audio','public.movie'],multipleSelectionsAllowed:false}).toString();}
catch(e){if(e.errorNumber!==-128)throw e;}JSON.stringify(path);'''
    value = json.loads(media.run(['/usr/bin/osascript', '-l', 'JavaScript', '-e', script]))
    if value is not None and not isinstance(value, str):
        raise media.Error('The file picker returned an invalid path.')
    return Path(value).expanduser().resolve() if value else None


class IntervalServer(LoopbackServer):
    assets = {
        '/': ('interval_analysis.html', 'text/html; charset=utf-8'),
        '/app.js': ('interval_analysis.js', 'text/javascript; charset=utf-8'),
        '/charts.js': ('interval_charts.js', 'text/javascript; charset=utf-8'),
        '/app.css': ('interval_analysis.css', 'text/css; charset=utf-8'),
    }
    upload_limit = RECORDING_LIMIT_BYTES

    def __init__(self, sessions_dir=None, directory=DIRECTORY):
        self.store = SessionStore(sessions_dir if sessions_dir is not None else BUILD / SESSIONS_DIRNAME)
        self.takes = {}
        self.analysis = None
        self.loaded = None
        self.working = False
        self._scratch = tempfile.TemporaryDirectory(prefix='interval-analysis-')
        self.work = Path(self._scratch.name)
        try:
            super().__init__(directory, 'Interval Analysis')
        except BaseException:
            self._scratch.cleanup()
            raise

    def busy(self):
        # The idle loop must not block on the lock held throughout analysis.
        return self.working

    @contextmanager
    def operation(self):
        with self.session_lock:
            if self.closing:
                raise media.Error('This session is closed.')
            self.working = True
            try:
                yield
            finally:
                self.working = False
                self.touch()

    def server_close(self):
        super().server_close()
        with self.session_lock:
            self.closing = True
            self._scratch.cleanup()

    def snapshot(self):
        with self.session_lock:
            loaded = None if self.loaded is None else {
                'id': self.loaded.id, 'title': self.loaded.title, 'notes': self.loaded.notes}
            return {'title': self.title,
                    'takes': {kind.value: self.takes[kind].recording.wire() if kind in self.takes
                              else None for kind in RecordingKind},
                    'analysis': self.analysis, 'loaded_session': loaded, 'sessions': self.store.list(),
                    'limits': {'bytes': RECORDING_LIMIT_BYTES, 'seconds': RECORDING_LIMIT_SECONDS,
                               'title': TITLE_LIMIT, 'notes': NOTES_LIMIT,
                               'max_offset': core.MAX_OFFSET_SECONDS},
                    'kinds': [kind.value for kind in RecordingKind],
                    'alignment_modes': list(core.ALIGNMENT_MODES)}

    def discard_take(self, take):
        # Disposal cannot roll back a committed replacement or mask a staging error.
        for path in (take.path, take.listening):
            with suppress(OSError):
                path.unlink(missing_ok=True)

    def replace_takes(self, replacements, *, analysis=None, loaded=None):
        previous = [self.takes[kind] for kind in replacements if kind in self.takes]
        self.takes = {**self.takes, **replacements}
        self.analysis, self.loaded = analysis, loaded
        for take in previous:
            self.discard_take(take)

    def receive_upload(self, route, data, headers):
        if not route.startswith('/upload/'):
            raise media.Error('Unknown request.')
        kind = RecordingKind(route[len('/upload/'):])
        mime = media_type(headers.get('Content-Type'))
        delay = finite_number(float(headers.get('X-Playback-Delay', '0')), 'Playback delay', 0, 5)
        if not 0 < len(data) <= self.upload_limit:
            raise media.Error('Recording is empty or exceeds the upload limit.')
        with self.operation():
            take = capture_take(io.BytesIO(data), kind, mime,
                                playback_delay=delay, work=self.work)
            self.replace_takes({kind: take})
            return self.snapshot()

    def get_resource(self, route):
        if not route.startswith('/take/'):
            raise media.Error('Unknown request.')
        kind = RecordingKind(route[len('/take/'):])
        with self.session_lock:
            if kind not in self.takes:
                raise media.Error('Record or choose this take first.')
            return self.takes[kind].listening.read_bytes(), 'audio/wav'

    def dispatch(self, route, value):
        if not isinstance(value, dict):
            raise media.Error('Expected a request object.')
        handlers = {'/choose': self.choose, '/analyze': self.analyze, '/save': self.save,
                    '/load': self.load, '/update': self.update, '/reveal': self.reveal}
        with self.operation():
            if route == '/close':
                self.closing = True
                return {'closed': True}
            handler = handlers.get(route)
            if handler is None:
                raise media.Error('Unknown request.')
            return handler(value)

    def choose(self, value):
        kind = RecordingKind(value['kind'])
        track = value.get('audio_track', 0)
        if type(track) is not int or track < 0:
            raise media.Error('Audio track must be a nonnegative integer.')
        source = choose_file()
        if source is None:
            return self.snapshot()
        mime = file_mime(source)
        if not 0 < source.stat().st_size <= RECORDING_LIMIT_BYTES:
            raise media.Error('Selected file is empty or exceeds the recording limit.')
        with source.open('rb') as original:
            take = capture_take(original, kind, mime, audio_track=track, work=self.work)
        self.replace_takes({kind: take})
        return self.snapshot()

    def require_takes(self):
        if set(self.takes) != set(RecordingKind):
            raise media.Error('Both reference and comparison takes are required.')

    def analyze(self, value):
        self.require_takes()
        alignment = value.get('alignment', 'auto')
        if alignment not in core.ALIGNMENT_MODES or alignment == 'hint':
            raise media.Error('Choose automatic or manual alignment.')
        manual = None
        if alignment == 'manual':
            manual = finite_number(value.get('offset_seconds'), 'Manual offset', -5, 5)
        reference, comparison = (self.takes[kind] for kind in RecordingKind)
        self.analysis = core.compare_tracks(reference.pitch, comparison.pitch,
                                            hint=comparison.recording.playback_delay, manual_offset=manual)
        # Updating notes must never silently save an analysis different from this one.
        self.loaded = None
        return self.snapshot()

    def save(self, value):
        self.require_takes()
        if self.analysis is None:
            raise media.Error('Analyze both takes before saving.')
        self.loaded = self.store.save(value['title'], value['notes'], self.takes, self.analysis)
        return self.snapshot()

    def restore_take(self, id, kind, expected):
        original, recording = self.store.open_recording(id, kind)
        with original:
            if recording != expected:
                raise media.Error('Saved session changed while loading. Try again.')
            return capture_take(original, kind, recording.mime,
                                audio_track=recording.audio_track,
                                playback_delay=recording.playback_delay, work=self.work)

    def load(self, value):
        session = self.store.load(value['id'])
        restored = {}
        try:
            for kind in RecordingKind:
                restored[kind] = self.restore_take(session.id, kind, session.recordings[kind])
        except BaseException:
            for take in restored.values():
                self.discard_take(take)
            raise
        analysis = session.analysis.wire() if session.analysis is not None else None
        self.replace_takes(restored, analysis=analysis, loaded=session)
        return self.snapshot()

    def update(self, value):
        if self.loaded is None or value.get('id') != self.loaded.id:
            raise media.Error('Load this session before updating it.')
        self.loaded = self.store.update(value['id'], value['title'], value['notes'])
        return self.snapshot()

    def reveal(self, value):
        self.store.reveal(value['id'])
        return {'revealed': True}


def print_summary(analysis):
    interval = analysis['interval']
    if interval is None:
        print('No interval answer: not enough overlapping singing.')
    else:
        direction = '' if interval['direction'] == 'unison' else ' ' + interval['direction']
        print(f"{interval['name']}{direction}: {interval['semitones']} semitones; "
              f"agreement {interval['agreement']:.0%}; median {interval['cents_median']:+.1f} cents")
        simple = analysis['simple_interval']
        if simple and (simple['semitones'], simple['agreement']) != (interval['semitones'], interval['agreement']):
            print(f"Ignoring octaves: {simple['name']} {simple['direction']} ({simple['agreement']:.0%})")
    alignment = analysis['alignment']
    print(f"Alignment: {alignment['offset_seconds']:+.4f} s ({alignment['mode']})")
    for warning in analysis['warnings']:
        print(f'Warning: {warning}')


def analyze_files(args):
    with tempfile.TemporaryDirectory(prefix='interval-files-') as scratch:
        work, takes = Path(scratch), {}
        for kind, source in zip(RecordingKind, args.files):
            source = source.expanduser().resolve()
            with source.open('rb') as original:
                takes[kind] = capture_take(original, kind, file_mime(source),
                                           args.audio_track, work=work)
        result = core.analyze_pair(*(takes[kind].samples for kind in RecordingKind),
                                   sample_rate=core.SAMPLE_RATE, hint=0, manual_offset=args.offset)
        if args.save:
            SessionStore(args.sessions_dir).save(args.title, args.notes, takes, result)
        if args.json:
            print(json.dumps(result, allow_nan=False))
        else:
            print_summary(result)


def launch_background(args):
    logs = BUILD / 'interval-analysis-jobs'
    logs.mkdir(parents=True, mode=0o700, exist_ok=True)
    command = [sys.executable, str(Path(__file__).resolve())]
    for flag in ('no_open', 'json', 'save'):
        if getattr(args, flag):
            command.append('--' + flag.replace('_', '-'))
    for flag in ('title', 'notes', 'audio_track', 'offset'):
        value = getattr(args, flag)
        if value is not None:
            command.append('--' + flag.replace('_', '-') + '=' + str(value))
    # abspath preserves a symlink in the final root component for the store to reject.
    command += ['--sessions-dir', os.path.abspath(args.sessions_dir.expanduser())]
    command += [str(path.expanduser().resolve()) for path in args.files]
    with tempfile.NamedTemporaryFile(dir=logs, prefix='interval-', suffix='.log', delete=False) as log:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        print(f'Interval Analysis started. Log: {log.name}')


def serve(args):
    with IntervalServer(args.sessions_dir) as server:
        url = server.url()
        print(url, flush=True)
        if not args.no_open:
            media.run(['/usr/bin/open', url])
        server.serve_until_closed(1800)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='*', type=Path)
    for flag in ('background', 'no-open', 'json', 'save'):
        parser.add_argument('--' + flag, action='store_true')
    parser.add_argument('--title', default='Untitled session')
    parser.add_argument('--notes', default='')
    parser.add_argument('--audio-track', type=int, default=0)
    parser.add_argument('--offset', type=float)
    parser.add_argument('--sessions-dir', type=Path, default=BUILD / SESSIONS_DIRNAME)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if len(args.files) not in (0, 2):
            raise media.Error('Provide exactly two files: reference, then comparison.')
        if args.audio_track < 0:
            raise media.Error('Audio track must be a nonnegative integer.')
        if args.offset is not None:
            finite_number(args.offset, 'Manual offset', -5, 5)
        if args.background:
            launch_background(args)
        else:
            convert_video.configure_tools()
            analyze_files(args) if args.files else serve(args)
    except (media.Error, OSError, ValueError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
