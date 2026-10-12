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
from conversion_jobs import ConversionJobs
from conversion_settings import ConversionSettings, VideoSettings, VIDEO_MODES, TONE_MAPS

DIRECTORY = Path(__file__).resolve().parent
APP = next((p for p in DIRECTORY.parents if p.suffix == '.app'), None)
BUILD = APP.parent if APP else DIRECTORY.parent / 'build'


class EditorServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, jobs):
        self.jobs = jobs
        self.token = secrets.token_urlsafe(32)
        self.last_request = time.monotonic()
        super().__init__(('127.0.0.1', 0), EditorHandler)
        self.origin = f'http://127.0.0.1:{self.server_port}'
        self.timeout = 1
        self.closing = False

    def snapshot(self):
        return {'files': [str(p) for p in self.jobs.files], 'presets': self.jobs.presets(),
                'ranges': effects.RANGES, 'video_modes': VIDEO_MODES, 'tone_maps': TONE_MAPS,
                'video_default': asdict(VideoSettings()), 'job': self.jobs.status()}

    def dispatch(self, route, value):
        if route in ('/convert', '/save'):
            settings = ConversionSettings.parse(value)
            if route == '/convert': self.jobs.start(settings); return {'started': True}
            return {'id': self.jobs.save(settings)}
        if route == '/reveal':
            outputs = self.jobs.status()['outputs']
            index = value.get('index') if isinstance(value, dict) else None
            if type(index) is not int or not 0 <= index < len(outputs):
                raise media.Error('Unknown output.')
            media.run(['/usr/bin/open', '-R', outputs[index]])
            return {'revealed': True}
        if route == '/close':
            if self.jobs.status()['running']: raise media.Error('Wait for conversion to finish before closing.')
            self.closing = True
            return {'closed': True}
        raise media.Error('Unknown request.')


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
            self.server.last_request = time.monotonic()
            self.reply(200, self.server.dispatch(self.path, value))
        except (media.Error, OSError, ValueError, TypeError, KeyError) as error:
            self.reply(400, {'error': str(error)})


def launch_background(args):
    logs = BUILD/'conversion-editor-jobs'; logs.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(Path(__file__).resolve())]
    for flag in ('finder_selection', 'choose', 'no_open'):
        if getattr(args, flag): command.append('--'+flag.replace('_', '-'))
    for key in ('config', 'output_dir'):
        if getattr(args, key): command += ['--'+key.replace('_', '-'), str(getattr(args, key).expanduser().resolve())]
    command += [str(p.expanduser().resolve()) for p in args.files]
    with tempfile.NamedTemporaryFile(dir=logs, prefix='editor-', suffix='.log', delete=False) as log:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        print(f'Editor started. Log: {log.name}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files', nargs='*', type=Path)
    for flag in ('finder-selection', 'choose', 'background', 'no-open'):
        parser.add_argument('--'+flag, action='store_true')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args(argv)
    os.umask(0o077)
    if args.background: launch_background(args); return
    convert_video.configure_tools()
    files = args.files or effects.select_videos(args.finder_selection)
    if not files: return
    for file in files: media.stream(media.probe(file), 'video')
    pointer = DIRECTORY/'presets-path.txt'
    config = args.config or (Path(pointer.read_text().strip()) if pointer.exists() else effects.DEFAULT_CONFIG)
    jobs = ConversionJobs(files, config, BUILD/'custom-conversion', args.output_dir)
    with EditorServer(jobs) as server:
        url = server.origin+'/#'+server.token
        print(url, flush=True)
        if not args.no_open: media.run(['/usr/bin/open', url])
        # This is a per-conversion session, not an always-on service.
        while not server.closing:
            server.handle_request()
            if not jobs.status()['running'] and time.monotonic()-server.last_request > 1800: break


if __name__ == '__main__':
    try: main()
    except (media.Error, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
