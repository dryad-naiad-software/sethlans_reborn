# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for the sync WSGI surface of ``web_ui/setup/gate.py``.

Covers ``setup_gate_wrapper_wsgi`` -- the Phase 3 sync dispatcher
introduced alongside the still-async ``setup_gate_wrapper``.  The
async wrapper is covered by ``test_setup_gate``; this file does
not retest it.  Module state is reset by the autouse fixture in
``conftest.py``.
"""

import io
import json
from typing import Any, Dict, List, Tuple

import pytest

from sethlans_worker_agent.web_ui.setup import gate
from sethlans_worker_agent.web_ui.setup.gate import (
    init_gate,
    mark_setup_complete,
    setup_gate_wrapper_wsgi,
)


# --- Minimal WSGI helpers ---

def make_environ(
    path: str = '/',
    method: str = 'GET',
    body: bytes = b'',
    content_type: str = '',
) -> Dict[str, Any]:
    env: Dict[str, Any] = {
        'REQUEST_METHOD': method,
        'PATH_INFO': path,
        'QUERY_STRING': '',
        'SERVER_NAME': 'localhost',
        'SERVER_PORT': '8080',
        'wsgi.url_scheme': 'http',
        'wsgi.input': io.BytesIO(body),
    }
    if body:
        env['CONTENT_LENGTH'] = str(len(body))
    if content_type:
        env['CONTENT_TYPE'] = content_type
    return env


class StartResponseRecorder:
    """Captures WSGI ``start_response`` invocations for assertion."""

    def __init__(self) -> None:
        self.status: str = ''
        self.headers: List[Tuple[str, str]] = []

    def __call__(self, status: str, headers: List[Tuple[str, str]]) -> None:
        self.status = status
        self.headers = list(headers)

    @property
    def status_code(self) -> int:
        return int(self.status.split(' ', 1)[0])

    def header(self, name: str) -> str:
        lower = name.lower()
        for k, v in self.headers:
            if k.lower() == lower:
                return v
        return ''


def drain(iterable) -> bytes:
    return b''.join(iterable)


def _sync_ok(body_bytes: bytes = b'ok', status: str = '200 OK'):
    """Build a sync WSGI inner that records calls and returns body."""
    calls: List[Dict[str, Any]] = []

    def inner(environ, start_response):
        calls.append(environ)
        start_response(status, [('Content-Type', 'text/plain')])
        return [body_bytes]

    return inner, calls


def _async_json(payload: bytes = b'{"ok": true}', status: int = 200):
    """Build an async ASGI inner that records calls and returns JSON."""
    calls: List[Dict[str, Any]] = []

    async def inner(scope, receive, send):
        calls.append(scope)
        await send({
            'type': 'http.response.start', 'status': status,
            'headers': [(b'content-type', b'application/json')],
        })
        await send({
            'type': 'http.response.body', 'body': payload,
            'more_body': False,
        })

    return inner, calls


async def _must_not_call(*_a, **_kw):  # pragma: no cover
    raise AssertionError('inner must not be called')


# --- Fast path: setup complete ---

class TestFastPathSetupComplete:
    def test_sync_inner_called_directly_when_complete(self):
        mark_setup_complete()
        inner, calls = _sync_ok()
        rec = StartResponseRecorder()
        body = drain(
            setup_gate_wrapper_wsgi(make_environ('/dashboard'), rec, inner),
        )
        assert len(calls) == 1
        assert rec.status_code == 200
        assert body == b'ok'

    def test_async_inner_driven_via_adapter_when_complete(self):
        mark_setup_complete()
        inner, calls = _async_json()
        rec = StartResponseRecorder()
        body = drain(
            setup_gate_wrapper_wsgi(make_environ('/dashboard'), rec, inner),
        )
        assert len(calls) == 1
        assert calls[0]['path'] == '/dashboard'
        assert rec.status_code == 200
        assert rec.header('Content-Type') == 'application/json'
        assert json.loads(body) == {'ok': True}


# --- Blocking path: setup incomplete, non-setup request ---

class TestBlockedDuringSetup:
    @pytest.mark.parametrize('path', ['/dashboard', '/api/jobs/'])
    def test_blocks_non_setup_path_with_503_json(self, path):
        rec = StartResponseRecorder()
        body = drain(
            setup_gate_wrapper_wsgi(
                make_environ(path), rec, _must_not_call,
            ),
        )
        assert rec.status_code == 503
        assert rec.header('Content-Type') == 'application/json'
        assert json.loads(body) == {'detail': 'Setup not complete.'}


# --- Allowed-prefix pass-through during setup ---

class TestAllowedPrefixesDuringSetup:
    @pytest.mark.parametrize(
        'path', ['/api/setup/status/', '/setup', '/api/setup/worker/enroll/'],
    )
    def test_setup_prefixes_pass_through_sync(self, path):
        inner, calls = _sync_ok(b'setup-ok')
        rec = StartResponseRecorder()
        body = drain(setup_gate_wrapper_wsgi(make_environ(path), rec, inner))
        assert calls and rec.status_code == 200
        assert body == b'setup-ok'

    def test_setup_prefix_passes_through_async(self):
        inner, calls = _async_json(b'{}', status=201)
        rec = StartResponseRecorder()
        body = drain(
            setup_gate_wrapper_wsgi(
                make_environ('/api/setup/worker/enroll/'), rec, inner,
            ),
        )
        assert calls and calls[0]['path'] == '/api/setup/worker/enroll/'
        assert rec.status_code == 201
        assert body == b'{}'


# --- Async adapter request-body plumbing and error handling ---

class TestAsyncAdapterBodyPlumbing:
    def test_request_body_flows_through_receive(self):
        mark_setup_complete()
        captured: List[bytes] = []

        async def inner(scope, receive, send):
            msg = await receive()
            captured.append(msg.get('body', b''))
            await send({
                'type': 'http.response.start', 'status': 200,
                'headers': [(b'content-type', b'application/json')],
            })
            await send({
                'type': 'http.response.body', 'body': b'{"seen": true}',
                'more_body': False,
            })

        payload = b'{"hello": "world"}'
        env = make_environ(
            '/echo', method='POST', body=payload,
            content_type='application/json',
        )
        rec = StartResponseRecorder()
        body = drain(setup_gate_wrapper_wsgi(env, rec, inner))
        assert captured == [payload]
        assert rec.status_code == 200
        assert json.loads(body) == {'seen': True}

    def test_async_inner_exception_propagates(self):
        mark_setup_complete()

        class Boom(RuntimeError):
            pass

        async def inner(scope, receive, send):
            raise Boom('inner blew up')

        rec = StartResponseRecorder()
        with pytest.raises(Boom):
            drain(
                setup_gate_wrapper_wsgi(
                    make_environ('/dashboard'), rec, inner,
                ),
            )

    def test_async_inner_that_never_responds_yields_500(self):
        mark_setup_complete()

        async def inner(scope, receive, send):
            return None  # protocol violation: no response sent

        rec = StartResponseRecorder()
        body = drain(
            setup_gate_wrapper_wsgi(make_environ('/dashboard'), rec, inner),
        )
        assert rec.status_code == 500
        assert body == b''


# --- Edge cases and module-state interactions ---

class TestEdgeCasesAndInitGate:
    @pytest.mark.parametrize('kwargs', [{'path': ''}, {}])
    def test_empty_or_missing_path_info_is_blocked(self, kwargs):
        env = make_environ(**kwargs) if kwargs else make_environ('/x')
        if not kwargs:
            env.pop('PATH_INFO', None)
        rec = StartResponseRecorder()
        body = drain(
            setup_gate_wrapper_wsgi(env, rec, _must_not_call),
        )
        assert rec.status_code == 503
        assert json.loads(body) == {'detail': 'Setup not complete.'}

    def test_init_gate_complete_opens_sync_fast_path(
        self, tmp_path, mocker,
    ):
        mocker.patch(
            'sethlans_worker_agent.web_ui.setup.gate.is_setup_complete',
            return_value=True)
        init_gate(tmp_path)
        inner, calls = _sync_ok()
        rec = StartResponseRecorder()
        drain(
            setup_gate_wrapper_wsgi(make_environ('/dashboard'), rec, inner))
        assert calls and rec.status_code == 200

    def test_init_gate_incomplete_keeps_sync_blocking(
        self, tmp_path, mocker,
    ):
        mocker.patch(
            'sethlans_worker_agent.web_ui.setup.gate.is_setup_complete',
            return_value=False)
        init_gate(tmp_path)
        assert gate._setup_complete is False
        rec = StartResponseRecorder()
        body = drain(setup_gate_wrapper_wsgi(
            make_environ('/dashboard'), rec, _must_not_call))
        assert rec.status_code == 503
        assert json.loads(body) == {'detail': 'Setup not complete.'}

    def test_mark_setup_complete_flips_sync_gate(self):
        assert gate._setup_complete is False
        inner, calls = _sync_ok()
        rec1 = StartResponseRecorder()
        drain(
            setup_gate_wrapper_wsgi(make_environ('/dashboard'), rec1, inner))
        assert rec1.status_code == 503 and calls == []
        mark_setup_complete()
        rec2 = StartResponseRecorder()
        drain(
            setup_gate_wrapper_wsgi(make_environ('/dashboard'), rec2, inner))
        assert calls and rec2.status_code == 200
