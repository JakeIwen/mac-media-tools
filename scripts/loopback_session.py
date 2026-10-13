"""Authenticated, short-lived loopback sessions for local media tools."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
import threading
import time

sys.dont_write_bytecode = True
import performance_audio as media


def request_object(data):
    try:
        value = json.loads(data)
    except (ValueError, RecursionError) as error:
        raise media.Error(f'Invalid request JSON: {error}') from error
    if not isinstance(value, dict):
        raise media.Error('Expected a request object.')
    return value


class LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True
    assets: dict[str, tuple[str, str]] = {}
    upload_limit = 0
    json_limit = 65536
    body_timeout = 30.0
    csp = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
           "img-src 'self' data:; media-src 'self' blob:; frame-ancestors 'none'")

    def __init__(self, directory: Path, title: str):
        self.directory = Path(directory)
        self.title = title
        self.token = secrets.token_urlsafe(32)
        self.session_lock = threading.RLock()
        self.last_request = time.monotonic()
        self.closing = False
        super().__init__(('127.0.0.1', 0), LoopbackHandler)
        self.origin = f'http://127.0.0.1:{self.server_port}'
        self.timeout = 1

    def url(self) -> str:
        return self.origin + '/#' + self.token

    def touch(self):
        with self.session_lock:
            self.last_request = time.monotonic()

    def snapshot(self) -> dict:
        raise NotImplementedError

    def dispatch(self, route, value: dict) -> dict:
        raise NotImplementedError

    def receive_upload(self, route, data: bytes, headers) -> dict:
        raise media.Error('Unknown request.')

    def get_resource(self, route) -> tuple[bytes, str]:
        raise media.Error('Unknown request.')

    def busy(self) -> bool:
        return False

    def serve_until_closed(self, idle_seconds=1800):
        while not self.closing:
            self.handle_request()
            if not self.busy() and time.monotonic() - self.last_request > idle_seconds:
                break

    def server_close(self):
        self.closing = True
        super().server_close()


class LoopbackHandler(BaseHTTPRequestHandler):
    timeout = 30

    def log_message(self, *args):
        pass  # Session tokens and private paths must not enter access logs.

    def reply(self, code, body, content_type='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body, allow_nan=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', self.server.csp)
        if self.close_connection:
            self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        hosts = self.headers.get_all('Host', [])
        origins = self.headers.get_all('Origin', [])
        tokens = self.headers.get_all('X-Editor-Token', [])
        valid_host = hosts == [self.server.origin.split('//', 1)[1]]
        valid_origin = not origins or origins == [self.server.origin]
        valid_token = len(tokens) == 1 and secrets.compare_digest(
            tokens[0].encode(), self.server.token.encode())
        return valid_host and valid_origin and valid_token

    def do_GET(self):
        if self.path not in self.server.assets and not self.authorized():
            self.reply(403, {'error': 'Session unavailable. Reopen the tool from BTT.'})
            return
        self.server.touch()
        try:
            if self.path in self.server.assets:
                name, content_type = self.server.assets[self.path]
                self.reply(200, (self.server.directory / name).read_bytes(), content_type)
            elif self.path == '/state':
                self.reply(200, self.server.snapshot())
            else:
                data, content_type = self.server.get_resource(self.path)
                self.reply(200, data, content_type)
        except (media.Error, OSError, ValueError, TypeError, KeyError) as error:
            self.reply(400, {'error': str(error)})

    def reject_body(self, code, message):
        self.close_connection = True
        self.reply(code, {'error': message})

    def body_length(self, is_json):
        if 'Transfer-Encoding' in self.headers:
            return self.reject_body(411, 'Transfer-Encoding is not supported.')
        lengths = self.headers.get_all('Content-Length', [])
        if not lengths:
            return self.reject_body(411, 'A Content-Length header is required.')
        if len(lengths) != 1:
            return self.reject_body(400, 'Expected one Content-Length header.')
        text = lengths[0].strip()
        if not text.isascii() or not text.isdecimal():
            return self.reject_body(400, 'Invalid Content-Length.')
        try:
            length = int(text)
        except ValueError:
            return self.reject_body(400, 'Invalid Content-Length.')
        limit = self.server.json_limit if is_json else self.server.upload_limit
        if length > limit or (not is_json and limit == 0):
            return self.reject_body(413, 'Request body is too large.')
        return length

    def read_body(self, length):
        deadline = time.monotonic() + self.server.body_timeout
        socket_timeout = self.connection.gettimeout()
        data = bytearray()
        try:
            while len(data) < length:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                timeout = remaining if socket_timeout is None else min(socket_timeout, remaining)
                self.connection.settimeout(timeout)
                # read1 performs at most one socket read, so dribbled bytes cannot reset the deadline.
                chunk = self.rfile.read1(min(length - len(data), 65536))
                if not chunk:
                    raise EOFError
                data.extend(chunk)
            if time.monotonic() > deadline:
                raise TimeoutError
        except (OSError, EOFError) as error:
            self.close_connection = True
            raise media.Error('Incomplete request body.') from error
        finally:
            self.connection.settimeout(socket_timeout)
        return bytes(data)

    def do_POST(self):
        if not self.authorized():
            self.reject_body(403, 'Session unavailable. Reopen the tool from BTT.')
            return
        self.server.touch()
        try:
            is_json = self.headers.get('Content-Type', '').lower().startswith('application/json')
            length = self.body_length(is_json)
            if length is None:
                return
            data = self.read_body(length)
            if is_json:
                result = self.server.dispatch(self.path, request_object(data))
            else:
                result = self.server.receive_upload(self.path, data, self.headers)
            self.server.touch()
            self.reply(200, result)
        except (media.Error, OSError, ValueError, TypeError, KeyError) as error:
            self.reply(400, {'error': str(error)})
