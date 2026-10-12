#!/usr/bin/env python3
"""Open a short-lived local editor for the selected performance videos."""
import argparse
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time

sys.dont_write_bytecode = True
import convert_video
import performance_audio as media
import performance_audio_effects as effects
import conversion_reports
from conversion_jobs import ConversionJobs
from conversion_settings import ConversionSettings, VideoSettings, VIDEO_MODES, TONE_MAPS
from media_selection import MediaSelection, SelectionOrigin, select_media
from media_task_control import TaskState

DIRECTORY = Path(__file__).resolve().parent
APP = next((p for p in DIRECTORY.parents if p.suffix == '.app'), None)
BUILD = APP.parent if APP else DIRECTORY.parent / 'build'


class EditorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, jobs, selection=None, initial=None, title='Custom conversion'):
        self.jobs = jobs
        self.selection = selection or MediaSelection(tuple(jobs.files), SelectionOrigin.ARGUMENTS)
        inspection = self.selection.inspect()
        self.selection_details = inspection.details
        self.selection_revision = 0
        self.signatures = inspection.signatures
        self.initial = initial
        self.title = title
        self.session_lock = threading.RLock()
        self.token = secrets.token_urlsafe(32)
        self.last_request = time.monotonic()
        super().__init__(('127.0.0.1', 0), EditorHandler)
        self.origin = f'http://127.0.0.1:{self.server_port}'
        self.timeout = 1
        self.closing = False

    def snapshot(self):
        with self.session_lock:
            presets, warnings = self.jobs.presets()
            return {'files': self.selection_details, 'selection_origin': self.selection.origin.value,
                    'selection_revision': self.selection_revision, 'presets': presets, 'warnings': warnings,
                    'initial': self.initial, 'title': self.title,
                    'task_states': {state.name: state.value for state in TaskState},
                    'ranges': effects.RANGES, 'video_modes': VIDEO_MODES, 'tone_maps': TONE_MAPS,
                    'video_default': asdict(VideoSettings()), 'job': self.jobs.status()}

    def dispatch(self, route, value):
        if route == '/choose':
            with self.session_lock: self.require_idle()
            selection = select_media()
            inspection = selection.inspect()
            with self.session_lock:
                self.require_idle()
                if selection.files: self.update_selection(selection, inspection)
                return self.snapshot()
        if route == '/cancel':
            with self.session_lock:
                if not self.jobs.job or value.get('job_id') != self.jobs.job.name:
                    raise media.Error('This is not the active job. Refresh its status before cancelling.')
                task = self.jobs.task
            return {'cancelled': task.cancel()}
        with self.session_lock:
            if self.closing: raise media.Error('This session is closed.')
            return self.dispatch_locked(route, value)

    def require_idle(self):
        if self.closing or self.jobs.is_running():
            raise media.Error('Wait for the current job to stop before changing files or closing.')

    def update_selection(self, selection, inspection):
        self.selection, self.selection_details = selection, inspection.details
        self.signatures = inspection.signatures
        self.jobs.files = list(selection.files)
        self.selection_revision += 1

    def dispatch_locked(self, route, value):
        if route == '/report':
            return self.read_job_report(value)
        if route == '/convert':
            self.require_idle()
            if value.get('selection_revision') != self.selection_revision:
                raise media.Error('File selection changed. Review the displayed files before starting.')
            if self.signatures != [media.fingerprint(p) for p in self.selection.files]:
                raise media.Error('A selected file changed. Choose it again to review the new version.')
            self.jobs.start(ConversionSettings.parse(value['settings']))
            return {'started': True}
        if route == '/save':
            return {'id': self.jobs.save(ConversionSettings.parse(value))}
        if route == '/remove':
            self.require_idle()
            index = value.get('index')
            if type(index) is not int or not 0 <= index < len(self.jobs.files): raise media.Error('Unknown file.')
            files = tuple(p for i, p in enumerate(self.selection.files) if i != index)
            selection = MediaSelection(files, self.selection.origin)
            self.update_selection(selection, selection.inspect())
            return self.snapshot()
        if route == '/reveal':
            outputs = self.jobs.status()['task']['outputs']
            index = value.get('index') if isinstance(value, dict) else None
            if type(index) is not int or not 0 <= index < len(outputs):
                raise media.Error('Unknown output.')
            media.run(['/usr/bin/open', '-R', outputs[index]])
            return {'revealed': True}
        if route == '/close':
            self.require_idle()
            self.closing = True
            return {'closed': True}
        raise media.Error('Unknown request.')

    def read_job_report(self, value):
        if not self.jobs.job or value.get('job_id') != self.jobs.job.name:
            raise media.Error('These reports belong to a different processing job.')
        progress = self.jobs.status()['progress']
        output_index, report_index = value.get('output_index'), value.get('report_index')
        if type(output_index) is not int or not 0 <= output_index < len(progress['report_ids']):
            raise media.Error('Unknown output report.')
        generation = progress['report_ids'][output_index]
        artifacts = conversion_reports.artifacts(generation)
        if type(report_index) is not int or not 0 <= report_index < len(artifacts):
            raise media.Error('Unknown report.')
        if value.get('name') != artifacts[report_index].name:
            raise media.Error('The report list changed. Refresh the reports before opening one.')
        return conversion_reports.read_report(generation, artifacts[report_index])


class EditorHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # File names and session tokens must not enter access logs.

    def reply(self, code, body, content_type='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        valid_host = self.headers.get('Host') == self.server.origin.split('//')[1]
        valid_origin = self.headers.get('Origin') in (None, self.server.origin)
        valid_token = secrets.compare_digest(self.headers.get('X-Editor-Token', ''), self.server.token)
        return valid_host and valid_origin and valid_token

    def do_GET(self):
        assets = {'/': ('conversion_editor.html', 'text/html; charset=utf-8'),
                  '/editor.js': ('conversion_editor.js', 'text/javascript; charset=utf-8'),
                  '/editor.css': ('conversion_editor.css', 'text/css; charset=utf-8')}
        if self.path in assets:
            name, kind = assets[self.path]
            self.reply(200, (DIRECTORY/name).read_bytes(), kind)
        elif self.path == '/state' and self.authorized():
            self.server.last_request = time.monotonic()
            try: self.reply(200, self.server.snapshot())
            except (media.Error, OSError, ValueError) as error: self.reply(400, {'error': str(error)})
        else: self.reply(403, {'error': 'Session unavailable. Reopen Custom conversion from BTT.'})

    def do_POST(self):
        if not self.authorized(): self.reply(403, {'error': 'Invalid editor session.'}); return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536: raise media.Error('Invalid request size.')
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict): raise media.Error('Expected a request object.')
            self.server.last_request = time.monotonic()
            self.reply(200, self.server.dispatch(self.path, value))
        except (media.Error, OSError, ValueError, TypeError, KeyError) as error:
            self.reply(400, {'error': str(error)})


def launch_background(args):
    logs = BUILD/'conversion-editor-jobs'; logs.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(Path(__file__).resolve())]
    for flag in ('finder_selection', 'choose', 'no_open', 'share'):
        if getattr(args, flag): command.append('--'+flag.replace('_', '-'))
    for key in ('config', 'output_dir'):
        if getattr(args, key): command += ['--'+key.replace('_', '-'), str(getattr(args, key).expanduser().resolve())]
    command += [str(p.expanduser().resolve()) for p in args.files]
    for key in ('mode', 'preset', 'resolution', 'ambience', 'reverb', 'audio_track'):
        if getattr(args, key) is not None: command += ['--'+key.replace('_', '-'), str(getattr(args, key))]
    with tempfile.NamedTemporaryFile(dir=logs, prefix='editor-', suffix='.log', delete=False) as log:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        print(f'Editor started. Log: {log.name}')


def launch_for_tool(mode, args):
    command = ['--mode', mode]
    for key in ('config', 'preset', 'ambience', 'reverb', 'audio_track', 'resolution', 'output_dir'):
        value = getattr(args, key, None)
        if value is not None: command += ['--'+key.replace('_', '-'), str(value)]
    for key in ('finder_selection', 'choose', 'background', 'share'):
        if getattr(args, key, False): command.append('--'+key.replace('_', '-'))
    command += [str(p) for p in args.videos]
    return main(command)


def initial_settings(args, config):
    name, preset = effects.selected_preset(args.preset, config, args.reverb, args.ambience)
    video = VideoSettings(mode='sdr', resolution=args.resolution, tone_map='mobius') if args.mode == 'video' else VideoSettings()
    settings = ConversionSettings(preset, video, args.mode != 'video', args.share or args.mode == 'video')
    return settings.wire(), name if args.mode == 'audio' else 'custom'


def serve(jobs, selection, initial, title, no_open):
    with EditorServer(jobs, selection, initial, title) as server:
        url = server.origin+'/#'+server.token
        print(url, flush=True)
        if not no_open: media.run(['/usr/bin/open', url])
        try:
            while not server.closing:
                server.handle_request()
                if not jobs.is_running() and time.monotonic()-server.last_request > 1800: break
        finally:
            if jobs.is_running(): jobs.cancel()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='*', type=Path)
    for flag in ('finder-selection', 'choose', 'background', 'no-open', 'share'):
        parser.add_argument('--'+flag, action='store_true')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--mode', choices=('custom', 'audio', 'video'), default='custom')
    parser.add_argument('--preset')
    parser.add_argument('--audio-track', type=int, default=0)
    parser.add_argument('--resolution', choices=convert_video.RESOLUTIONS, default='1080p')
    parser.add_argument('--ambience', type=float)
    parser.add_argument('--reverb', type=float)
    args = parser.parse_args(argv)
    os.umask(0o077)
    if args.background: launch_background(args); return
    convert_video.configure_tools()
    selection = select_media(args.finder_selection, args.files)
    if not selection.files: return
    pointer = DIRECTORY/'presets-path.txt'
    config = args.config or (Path(pointer.read_text().strip()) if pointer.exists() else effects.DEFAULT_CONFIG)
    initial, slug = initial_settings(args, config)
    jobs = ConversionJobs(selection.files, config, BUILD/'custom-conversion', args.output_dir, args.audio_track, slug)
    title = {'custom': 'Custom conversion', 'audio': 'Performance Audio', 'video': 'Convert Video'}[args.mode]
    serve(jobs, selection, initial, title, args.no_open)


if __name__ == '__main__':
    try: main()
    except (media.Error, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
