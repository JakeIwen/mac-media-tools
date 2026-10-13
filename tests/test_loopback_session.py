"""Exercise loopback sessions through HTTP and malformed raw socket requests."""
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from loopback_session import LoopbackHandler, LoopbackServer
import performance_audio as media


class ShortTimeoutHandler(LoopbackHandler):
    timeout = 0.25


class SampleServer(LoopbackServer):
    assets = {'/': ('index.html', 'text/html; charset=utf-8')}
    upload_limit = 1024
    body_timeout = 1

    def __init__(self, directory):
        super().__init__(directory, 'Loopback test')
        self.RequestHandlerClass = ShortTimeoutHandler
        self.uploads = []
        self.dispatches = []
        self.working = threading.Event()
        self.busy_checked = threading.Event()

    def snapshot(self):
        return {'title': self.title}

    def dispatch(self, route, value):
        self.dispatches.append((route, value))
        if route == '/echo':
            return {'value': value}
        if route == '/close':
            self.closing = True
            return {'closed': True}
        if route == '/error':
            errors = {'media': media.Error, 'os': OSError, 'value': ValueError,
                      'type': TypeError, 'key': KeyError}
            raise errors[value['kind']]('Request failed.')
        raise media.Error('Unknown request.')

    def receive_upload(self, route, data, headers):
        if route != '/upload':
            return super().receive_upload(route, data, headers)
        self.uploads.append(data)
        return {'length': len(data), 'type': headers.get('Content-Type')}

    def get_resource(self, route):
        if route == '/resource':
            return b'local resource', 'application/octet-stream'
        return super().get_resource(route)

    def busy(self):
        self.busy_checked.set()
        return self.working.is_set()


class SessionFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'index.html').write_text('<h1>Loopback test</h1>')

    def new_server(self):
        server = SampleServer(self.root)
        self.addCleanup(server.server_close)
        return server

    def json_request(self, server, route, value=None, headers=None):
        data = None if value is None else json.dumps(value).encode()
        request = Request(server.origin + route, data=data, headers={
            'X-Editor-Token': server.token, 'Content-Type': 'application/json', **(headers or {})})
        with urlopen(request, timeout=3) as response:
            return json.load(response)


class LoopbackRequestTests(SessionFixture):
    def setUp(self):
        super().setUp()
        self.server = self.new_server()
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.thread.join(2)
        self.assertFalse(self.thread.is_alive())

    def open_raw(self, headers, body=b'', route='/upload'):
        connection = socket.create_connection(self.server.server_address, timeout=3)
        self.addCleanup(connection.close)
        base = [('Host', self.server.origin.split('//')[1]),
                ('X-Editor-Token', self.server.token)]
        lines = [f'POST {route} HTTP/1.1']
        lines += [f'{name}: {value}' for name, value in base + headers]
        connection.sendall(('\r\n'.join(lines) + '\r\n\r\n').encode() + body)
        return connection

    def raw_response(self, connection):
        with http.client.HTTPResponse(connection) as response:
            response.begin()
            return response.status, json.load(response), response.headers

    def test_asset_without_token_and_response_security_headers(self):
        with urlopen(self.server.origin + '/', timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b'<h1>Loopback test</h1>')
            self.assertEqual(response.headers['Content-Type'], 'text/html; charset=utf-8')
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')
            self.assertEqual(response.headers['Content-Security-Policy'], self.server.csp)

    def test_state_requires_token_and_touches_activity(self):
        before = self.server.last_request
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.server.origin + '/state', timeout=3)
        with caught.exception as response:
            self.assertEqual(response.code, 403)
            self.assertEqual(json.load(response), {
                'error': 'Session unavailable. Reopen the tool from BTT.'})
        self.assertEqual(self.json_request(self.server, '/state'), {'title': 'Loopback test'})
        self.assertGreater(self.server.last_request, before)

    def test_wrong_origin_host_and_token_are_forbidden(self):
        bad_headers = ({'Origin': 'https://foreign.invalid'}, {'Host': 'foreign.invalid'},
                       {'X-Editor-Token': 'wrong'}, {'X-Editor-Token': '\xe9'})
        for headers in bad_headers:
            for value in (None, {}):
                with self.subTest(headers=headers, value=value):
                    with self.assertRaises(HTTPError) as caught:
                        self.json_request(self.server, '/state' if value is None else '/echo',
                                          value, headers=headers)
                    with caught.exception as response:
                        self.assertEqual(response.code, 403)
        self.assertEqual(self.server.dispatches, [])

    def test_post_without_token_is_forbidden(self):
        request = Request(self.server.origin + '/echo', data=b'{}',
                          headers={'Content-Type': 'application/json'})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        with caught.exception as response:
            self.assertEqual(response.code, 403)
        self.assertEqual(self.server.dispatches, [])

    def test_json_dispatch_round_trip_with_same_origin(self):
        value = {'nested': {'number': 3}, 'text': 'a recording'}
        result = self.json_request(self.server, '/echo', value,
                                   {'Origin': self.server.origin,
                                    'Content-Type': 'application/json; charset=utf-8'})
        self.assertEqual(result, {'value': value})
        self.assertEqual(self.server.dispatches, [('/echo', value)])
        self.assertEqual(self.server.uploads, [])

    def test_raw_upload_round_trip(self):
        data = b'\x00\xffsynthetic bytes'
        request = Request(self.server.origin + '/upload', data=data, headers={
            'X-Editor-Token': self.server.token, 'Content-Type': 'audio/wav'})
        with urlopen(request, timeout=3) as response:
            self.assertEqual(json.load(response), {'length': len(data), 'type': 'audio/wav'})
        self.assertEqual(self.server.uploads, [data])
        self.assertEqual(self.server.dispatches, [])

    def test_authenticated_resource_and_unknown_route(self):
        with self.assertRaises(HTTPError) as caught:
            urlopen(self.server.origin + '/resource', timeout=3)
        with caught.exception as response:
            self.assertEqual(response.code, 403)
        request = Request(self.server.origin + '/resource',
                          headers={'X-Editor-Token': self.server.token})
        with urlopen(request, timeout=3) as response:
            self.assertEqual(response.read(), b'local resource')
            self.assertEqual(response.headers['Content-Type'], 'application/octet-stream')
        with self.assertRaises(HTTPError) as caught:
            self.json_request(self.server, '/unknown')
        with caught.exception as response:
            self.assertEqual(response.code, 400)
            self.assertEqual(json.load(response), {'error': 'Unknown request.'})

    def test_oversized_declared_body_is_rejected_without_receiving_body(self):
        for content_type, length in (('audio/wav', 1025), ('application/json', 65537)):
            with self.subTest(content_type=content_type):
                connection = self.open_raw([('Content-Type', content_type),
                                            ('Content-Length', str(length))])
                status, value, headers = self.raw_response(connection)
                self.assertEqual(status, 413)
                self.assertIn('error', value)
                self.assertEqual(headers['Connection'], 'close')
                self.assertEqual(connection.recv(1), b'')
        self.assertEqual(self.server.uploads, [])
        self.assertEqual(self.server.dispatches, [])

    def test_upload_limit_zero_disables_even_empty_raw_uploads(self):
        self.server.upload_limit = 0
        connection = self.open_raw([('Content-Length', '0')])
        self.assertEqual(self.raw_response(connection)[0], 413)
        self.assertEqual(self.server.uploads, [])

    def test_chunked_transfer_encoding_is_rejected(self):
        for extra in ([], [('Content-Length', '0')]):
            with self.subTest(extra=extra):
                connection = self.open_raw([('Transfer-Encoding', 'chunked')] + extra)
                status, value, headers = self.raw_response(connection)
                self.assertEqual(status, 411)
                self.assertIn('error', value)
                self.assertEqual(headers['Connection'], 'close')
        self.assertEqual(self.server.uploads, [])

    def test_content_length_must_be_present_single_and_nonnegative(self):
        cases = [([], 411), ([('Content-Length', '0'), ('Content-Length', '0')], 400)]
        cases += [([('Content-Length', value)], 400)
                  for value in ('-1', '+1', '1.5', 'junk', '', '1, 1', '1_0', '1' * 5000)]
        for headers, expected in cases:
            with self.subTest(headers=headers):
                connection = self.open_raw(headers)
                status, value, response_headers = self.raw_response(connection)
                self.assertEqual(status, expected)
                self.assertIn('error', value)
                self.assertEqual(response_headers['Connection'], 'close')
        self.assertEqual(self.server.uploads, [])

    def test_exact_and_zero_length_uploads_are_valid(self):
        for data in (b'', b'x' * self.server.upload_limit):
            with self.subTest(length=len(data)):
                connection = self.open_raw([('Content-Length', str(len(data)))], data)
                status, value, _ = self.raw_response(connection)
                self.assertEqual(status, 200)
                self.assertEqual(value['length'], len(data))
        self.assertEqual(self.server.uploads, [b'', b'x' * self.server.upload_limit])

    def test_truncated_body_is_never_dispatched(self):
        for content_type in ('audio/wav', 'application/json'):
            with self.subTest(content_type=content_type):
                connection = self.open_raw([('Content-Length', '20'),
                                            ('Content-Type', content_type)], b'12345')
                connection.shutdown(socket.SHUT_WR)
                status, value, headers = self.raw_response(connection)
                self.assertEqual(status, 400)
                self.assertEqual(value, {'error': 'Incomplete request body.'})
                self.assertEqual(headers['Connection'], 'close')
        self.assertEqual(self.server.uploads, [])
        self.assertEqual(self.server.dispatches, [])

    def test_stalled_body_times_out_without_partial_dispatch(self):
        start = time.monotonic()
        connection = self.open_raw([('Content-Length', '20')], b'12345')
        status, value, headers = self.raw_response(connection)
        self.assertEqual(status, 400)
        self.assertEqual(value, {'error': 'Incomplete request body.'})
        self.assertEqual(headers['Connection'], 'close')
        self.assertLess(time.monotonic() - start, self.server.body_timeout + 0.75)
        self.assertEqual(self.server.uploads, [])

    def test_absolute_deadline_is_not_reset_by_dribbled_bytes(self):
        connection = self.open_raw([('Content-Length', '100')], b'12345')
        stop = threading.Event()

        def dribble():
            while not stop.wait(0.05):
                try:
                    connection.sendall(b'x')
                except OSError:
                    break

        writer = threading.Thread(target=dribble, daemon=True)
        start = time.monotonic()
        writer.start()
        try:
            status, value, headers = self.raw_response(connection)
        finally:
            stop.set()
            writer.join(2)
        elapsed = time.monotonic() - start
        self.assertEqual(status, 400)
        self.assertEqual(value, {'error': 'Incomplete request body.'})
        self.assertEqual(headers['Connection'], 'close')
        self.assertGreater(elapsed, 0.75)
        self.assertLess(elapsed, self.server.body_timeout + 0.75)
        self.assertEqual(self.server.uploads, [])

    def test_fragmented_body_dispatches_only_the_declared_bytes(self):
        connection = self.open_raw([('Content-Length', '10')], b'12345')
        connection.sendall(b'67890extra bytes')
        status, value, _ = self.raw_response(connection)
        self.assertEqual(status, 200)
        self.assertEqual(value['length'], 10)
        self.assertEqual(self.server.uploads, [b'1234567890'])

    def test_invalid_json_and_non_object_are_not_dispatched(self):
        for data in (b'{broken', b'[]', b'null', b'1', b'"text"', b'\xff'):
            with self.subTest(data=data):
                connection = self.open_raw([('Content-Length', str(len(data))),
                                            ('Content-Type', 'application/json')], data, '/echo')
                status, value, _ = self.raw_response(connection)
                self.assertEqual(status, 400)
                self.assertIn('error', value)
        self.assertEqual(self.server.dispatches, [])

    def test_deep_json_and_oversized_integer_return_json_errors_without_dispatch(self):
        bodies = (b'{"x":' + b'[' * 20000 + b'0' + b']' * 20000 + b'}',
                  b'{"x":' + b'9' * 10000 + b'}')
        for data in bodies:
            with self.subTest(length=len(data)):
                self.assertLess(len(data), self.server.json_limit)
                connection = self.open_raw([('Content-Length', str(len(data))),
                                            ('Content-Type', 'application/json')], data, '/echo')
                status, value, headers = self.raw_response(connection)
                self.assertEqual(status, 400)
                self.assertEqual(headers['Content-Type'], 'application/json')
                self.assertIn('Invalid request JSON', value['error'])
                self.assertLess(len(value['error']), 500)
                self.assertEqual(self.json_request(self.server, '/state'), {'title': 'Loopback test'})
        self.assertEqual(self.server.dispatches, [])
        self.assertEqual(self.json_request(self.server, '/echo', {'ok': True}), {'value': {'ok': True}})

    def test_dispatch_errors_become_bad_requests(self):
        for kind in ('media', 'os', 'value', 'type', 'key'):
            with self.subTest(kind=kind):
                with self.assertRaises(HTTPError) as caught:
                    self.json_request(self.server, '/error', {'kind': kind})
                with caught.exception as response:
                    self.assertEqual(response.code, 400)
                    self.assertIn('Request failed.', json.load(response)['error'])

    def test_unknown_upload_uses_default_error(self):
        connection = self.open_raw([('Content-Length', '0')], route='/unknown')
        status, value, _ = self.raw_response(connection)
        self.assertEqual(status, 400)
        self.assertEqual(value, {'error': 'Unknown request.'})


class LoopbackLifecycleTests(SessionFixture):
    def start_session(self, server, idle_seconds=1800):
        thread = threading.Thread(target=server.serve_until_closed,
                                  kwargs={'idle_seconds': idle_seconds}, daemon=True)
        thread.start()
        self.addCleanup(self.finish_session, server, thread)
        return thread

    def finish_session(self, server, thread):
        server.closing = True
        thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_loopback_address_url_and_monotonic_touch(self):
        with LoopbackServer(self.root, 'Base session') as server:
            self.assertEqual(server.server_address[0], '127.0.0.1')
            self.assertEqual(server.timeout, 1)
            self.assertEqual(server.url(), server.origin + '/#' + server.token)
            self.assertFalse(server.busy())
            self.assertGreaterEqual(len(server.token), 32)
            with patch('loopback_session.time.monotonic', return_value=123.5):
                server.touch()
            self.assertEqual(server.last_request, 123.5)

    def test_close_dispatch_returns_without_another_connection(self):
        server = self.new_server()
        thread = self.start_session(server)
        self.assertEqual(self.json_request(server, '/close', {}), {'closed': True})
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(server.closing)

    def test_idle_session_expires_without_any_requests(self):
        server = self.new_server()
        start = time.monotonic()
        thread = self.start_session(server, idle_seconds=0.5)
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(time.monotonic() - start, 0.5)

    def test_busy_hook_postpones_idle_expiry(self):
        server = self.new_server()
        server.working.set()
        thread = self.start_session(server, idle_seconds=0.5)
        self.assertTrue(server.busy_checked.wait(2))
        self.assertTrue(thread.is_alive())
        self.assertEqual(self.json_request(server, '/state'), {'title': 'Loopback test'})
        server.working.clear()
        thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_context_manager_releases_listener_even_on_exception(self):
        server = self.new_server()
        address = server.server_address
        with self.assertRaisesRegex(ValueError, 'stop'):
            with server:
                raise ValueError('stop')
        self.assertEqual(server.fileno(), -1)
        with self.assertRaises(OSError):
            socket.create_connection(address, timeout=0.5)
        server.server_close()

    def test_server_close_releases_listener(self):
        server = self.new_server()
        server.server_close()
        self.assertEqual(server.fileno(), -1)
        self.assertTrue(server.closing)


if __name__ == '__main__':
    unittest.main()
