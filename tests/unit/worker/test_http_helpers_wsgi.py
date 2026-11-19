# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``web_ui/http_helpers_wsgi.py``.

Covers the 4 KB ``MAX_REQUEST_BODY`` cap (both ``CONTENT_LENGTH``
short-circuit and read-beyond-cap detection), happy-path JSON
parsing, error paths, and that every status code the web UI sends has
a proper reason phrase in ``_STATUS_LINES`` (#265).
"""

import io
import json
import re
import tokenize
from pathlib import Path

import sethlans_worker_agent.web_ui as web_ui_pkg
from sethlans_worker_agent.web_ui.http_helpers_wsgi import (
    _STATUS_LINES,
    MAX_REQUEST_BODY,
    parse_json_body_wsgi,
    read_body_wsgi,
    send_html_file_wsgi,
    send_json_wsgi,
)

from tests.unit.worker._wsgi_helpers import (
    StartResponseCapture,
    environ as _environ,
)

_WEB_UI_DIR = Path(web_ui_pkg.__file__).resolve().parent


# -------------------------------------------------------------------
# parse_json_body_wsgi
# -------------------------------------------------------------------

class TestParseJsonBodyWsgi:
    def test_happy_path_small_body(self):
        body = json.dumps({'key': 'value', 'n': 42}).encode()
        data, err = parse_json_body_wsgi(_environ(body))
        assert err is None
        assert data == {'key': 'value', 'n': 42}

    def test_oversize_via_content_length_short_circuits(self):
        """5 KB body with CONTENT_LENGTH header -> 413."""
        # The short-circuit path: we declare 5000 but the stream
        # itself stays empty.  This asserts read_body_wsgi never
        # needs to touch the stream to reject the payload.
        env = {
            'wsgi.input': io.BytesIO(b''),
            'CONTENT_LENGTH': '5000',
        }
        data, err = parse_json_body_wsgi(env)
        assert data is None
        assert err == ({'error': 'Request body too large'}, 413)

    def test_oversize_without_content_length_detected_via_read(self):
        """5 KB body, no CONTENT_LENGTH -> detected via read-cap."""
        big = b'x' * 5000
        env = {'wsgi.input': io.BytesIO(big)}
        data, err = parse_json_body_wsgi(env)
        assert data is None
        assert err == ({'error': 'Request body too large'}, 413)

    def test_exactly_at_cap_accepted(self):
        """4096 bytes of valid JSON should be accepted."""
        # Build JSON whose encoded length is exactly the cap.
        filler_len = MAX_REQUEST_BODY - len('{"d": ""}')
        payload = {'d': 'x' * filler_len}
        body = json.dumps(payload).encode()
        assert len(body) == MAX_REQUEST_BODY
        data, err = parse_json_body_wsgi(_environ(body))
        assert err is None
        assert data == payload

    def test_malformed_json_returns_400(self):
        env = _environ(b'{not json}')
        data, err = parse_json_body_wsgi(env)
        assert data is None
        assert err == ({'error': 'Invalid JSON'}, 400)

    def test_empty_body_returns_400(self):
        env = _environ(b'')
        data, err = parse_json_body_wsgi(env)
        assert data is None
        assert err == ({'error': 'Invalid JSON'}, 400)

    def test_json_array_accepted(self):
        body = json.dumps([1, 2, 3]).encode()
        data, err = parse_json_body_wsgi(_environ(body))
        assert err is None
        assert data == [1, 2, 3]

    def test_missing_wsgi_input_returns_400(self):
        data, err = parse_json_body_wsgi({})
        assert data is None
        assert err == ({'error': 'Invalid JSON'}, 400)

    def test_malformed_content_length_falls_back_to_read(self):
        body = json.dumps({'ok': True}).encode()
        env = {
            'wsgi.input': io.BytesIO(body),
            'CONTENT_LENGTH': 'not-a-number',
        }
        data, err = parse_json_body_wsgi(env)
        assert err is None
        assert data == {'ok': True}


# -------------------------------------------------------------------
# read_body_wsgi
# -------------------------------------------------------------------

class TestReadBodyWsgi:
    def test_reads_small_body(self):
        assert read_body_wsgi(_environ(b'hello')) == b'hello'

    def test_empty_environ_returns_empty(self):
        assert read_body_wsgi({}) == b''

    def test_never_reads_beyond_cap_plus_one(self):
        big = b'x' * 100_000
        env = {'wsgi.input': io.BytesIO(big)}
        result = read_body_wsgi(env)
        # Must be at most cap+1 so caller can detect oversize
        # without buffering the whole payload.
        assert len(result) == MAX_REQUEST_BODY + 1


# -------------------------------------------------------------------
# send_json_wsgi
# -------------------------------------------------------------------

class TestSendJsonWsgi:
    def test_sends_correct_headers_and_body(self):
        sr = StartResponseCapture()
        data = {'key': 'value', 'number': 42}
        body_iter = send_json_wsgi(sr, data)
        body = b''.join(body_iter)

        assert sr.status == '200 OK'
        headers = sr.header_dict()
        assert headers['content-type'] == 'application/json'
        assert headers['content-length'] == str(len(body))
        assert json.loads(body) == data

    def test_413_status_line(self):
        sr = StartResponseCapture()
        list(send_json_wsgi(sr, {'error': 'too big'}, 413))
        assert sr.status == '413 Request Entity Too Large'

    def test_400_status_line(self):
        sr = StartResponseCapture()
        list(send_json_wsgi(sr, {'error': 'bad'}, 400))
        assert sr.status == '400 Bad Request'

    def test_404_status_line(self):
        sr = StartResponseCapture()
        list(send_json_wsgi(sr, {'error': 'gone'}, 404))
        assert sr.status == '404 Not Found'

    def test_401_status_line(self):
        """#265: 401 used to fall back to ``401 Status``."""
        sr = StartResponseCapture()
        list(send_json_wsgi(sr, {'error': 'Unauthorized'}, 401))
        assert sr.status == '401 Unauthorized'

    def test_503_status_line(self):
        """#265: 503 used to fall back to ``503 Status``."""
        sr = StartResponseCapture()
        list(send_json_wsgi(sr, {'error': 'shutting down'}, 503))
        assert sr.status == '503 Service Unavailable'


# -------------------------------------------------------------------
# _STATUS_LINES coverage of every code the web UI sends (#265)
# -------------------------------------------------------------------

# Integer literals that look like 2xx, 4xx or 5xx status codes.  3xx is
# left out: the web UI never redirects, and ``channel_timeout=300`` in
# ``server.py`` would otherwise read as a status code.
_STATUS_LIKE = re.compile(r'^[245]\d\d$')


def _status_codes_in_web_ui():
    """Return ``{code: [where, ...]}`` for status-like literals in web_ui.

    Tokenizes each module so comments and docstrings (issue numbers,
    prose such as "returns 503") are ignored; only real integer
    literals in code count.
    """
    found = {}
    for path in sorted(_WEB_UI_DIR.rglob('*.py')):
        source = path.read_text(encoding='utf-8')
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok in tokens:
            if tok.type == tokenize.NUMBER and _STATUS_LIKE.match(tok.string):
                where = f'{path.relative_to(_WEB_UI_DIR)}:{tok.start[0]}'
                found.setdefault(int(tok.string), []).append(where)
    return found


class TestStatusLines:
    def test_every_entry_matches_its_code(self):
        for code, line in _STATUS_LINES.items():
            assert line.startswith(f'{code} ')
            assert line != f'{code} Status'

    def test_scan_finds_known_codes(self):
        """Guard against the scan silently finding nothing."""
        found = _status_codes_in_web_ui()
        assert {200, 400, 401, 404, 503} <= set(found)

    def test_every_sent_code_has_a_reason_phrase(self):
        """Every status-like integer literal under ``web_ui/`` has a ``_STATUS_LINES`` entry.

        Any integer literal that looks like a 2xx, 4xx or 5xx code counts, so a
        harmless literal such as ``timeout=500`` would need to be written
        differently (for example ``5 * 100``) or excluded from the scan.
        """
        found = _status_codes_in_web_ui()
        missing = {
            code: where for code, where in found.items()
            if code not in _STATUS_LINES
        }
        assert not missing, (
            'Status codes sent by web_ui/ with no entry in '
            f'http_helpers_wsgi._STATUS_LINES: {missing}'
        )


# -------------------------------------------------------------------
# send_html_file_wsgi
# -------------------------------------------------------------------

class TestSendHtmlFileWsgi:
    def test_reads_file_and_sends_html(self, tmp_path):
        html_file = tmp_path / 'test.html'
        html_file.write_text(
            '<html><body>Hello</body></html>', encoding='utf-8',
        )

        sr = StartResponseCapture()
        body = b''.join(send_html_file_wsgi(sr, str(html_file)))

        assert sr.status == '200 OK'
        headers = sr.header_dict()
        assert headers['content-type'] == 'text/html; charset=utf-8'
        assert headers['content-length'] == str(len(body))
        assert b'Hello' in body

    def test_returns_404_json_for_missing_file(self):
        sr = StartResponseCapture()
        body = b''.join(
            send_html_file_wsgi(sr, '/nonexistent/file.html'),
        )
        assert sr.status == '404 Not Found'
        payload = json.loads(body)
        assert payload == {'error': 'Not Found'}
