# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The manager step of a Blender install (BS-WK-5, BS-WK-6, BS-WK-10).

``blender_from_manager.fetch`` with the pinned session, the token, the request (``send_without_redirects``,
tested with ``shared.blender_sources``) and the verified body write faked: no network. Each answer the
manager cannot supply ends in one WARNING and ``UNAVAILABLE`` (the caller goes online at once) and leaves
no file behind.
"""
import json
import logging

import pytest
import requests

from shared.blender_sources.errors import SourceFailure
from sethlans_worker_agent import blender_from_manager, config, tls_adapter
from sethlans_worker_agent.blender_from_manager import ManagerAnswer, ManagerResult

VERSION, PLATFORM = '4.5.8', 'linux-x64'
FILE = 'blender-4.5.8-linux-x64.tar.xz'
SHA = 'ab' * 32
BASE = 'https://mgr.test:8080/api/'
META_URL = f'{BASE}blender-builds/{VERSION}/{PLATFORM}/'
ARCHIVE_URL = f'{META_URL}archive/'
GOOD = {'version': VERSION, 'platform': PLATFORM, 'file_name': FILE, 'sha256': SHA, 'size': 7}
AUTH = {'Authorization': 'Token t0ken'}


class FakeResponse:
    def __init__(self, status, body=b''):
        self.status_code = status
        self.headers = {}
        self.body = json.dumps(body).encode() if isinstance(body, dict) else body
        self.closed = False

    def iter_content(self, chunk_size=1):
        yield self.body

    def close(self):
        self.closed = True


class Redirect(FakeResponse):
    """A 3xx whose body must stay unread (reading it fails the test)."""

    def __init__(self, status):
        super().__init__(status)
        self.headers = {'Location': 'https://elsewhere.test/blender.tar.xz'}

    def iter_content(self, chunk_size=1):
        raise AssertionError(f'the body of an HTTP {self.status_code} was read')


class FakeSession:
    """Answers by URL (a response, or an exception to raise); records each request's URL, timeout and headers."""

    def __init__(self, answers, pinned=True):
        self.answers, self.pinned, self.calls = answers, pinned, []

    def get_adapter(self, _url):
        return tls_adapter.PinningHTTPAdapter.__new__(tls_adapter.PinningHTTPAdapter) if self.pinned else object()


def _send(session, url, timeout, headers=None):
    """Stands in for ``send_without_redirects``: the session's answer for ``url``."""
    session.calls.append((url, timeout, headers))
    answer = session.answers[url]
    if isinstance(answer, Exception):
        raise answer
    return answer


@pytest.fixture
def manager(monkeypatch, tmp_path):
    """Enrolled worker; ``manager.answer(answers, pinned=True)`` installs the session; ``write`` is the body write."""
    monkeypatch.setattr(config, 'MANAGER_API_URL', BASE)
    monkeypatch.setattr(blender_from_manager, 'get_auth_headers', lambda: dict(AUTH))
    monkeypatch.setattr(blender_from_manager, 'send_without_redirects', _send)
    state = type('State', (), {})()
    state.writes = []
    state.write_error = None

    def write(response, part, sha256, max_bytes, stop):
        state.writes.append((response, part, sha256, max_bytes, stop))
        part.write_bytes(b'archive')
        if state.write_error is not None:
            raise state.write_error
    monkeypatch.setattr(blender_from_manager, 'write_verified_body', write)

    def answer(answers, pinned=True):
        state.session = FakeSession(answers, pinned)
        monkeypatch.setattr(tls_adapter, 'get_session', lambda: state.session)
    state.answer = answer
    state.dest = tmp_path / 'blender'
    return state


def _warnings(caplog):
    return [r for r in caplog.records if r.name == blender_from_manager.__name__ and r.levelno == logging.WARNING]


def test_a_ready_build_is_streamed_with_its_size_as_the_limit(manager, caplog):
    """BS-WK-5, BS-WK-6: 200 metadata, then the archive over the same pinned session with the token, each
    sent through ``send_without_redirects`` (no redirect followed; it adds ``Accept-Encoding: identity``);
    the body is checked against the manager's sha256 with ``size`` as the exact byte limit, then renamed
    into place."""
    archive = FakeResponse(200)
    manager.answer({META_URL: FakeResponse(200, GOOD), ARCHIVE_URL: archive})
    stop = object()

    result = blender_from_manager.fetch(VERSION, PLATFORM, manager.dest, stop=stop)

    assert result == ManagerResult(ManagerAnswer.SUPPLIED, state='ready', archive=manager.dest / FILE)
    assert [p.name for p in manager.dest.iterdir()] == [FILE]
    assert manager.writes == [(archive, manager.dest / f'{FILE}.part', SHA, 7, stop)]
    assert [(url, headers) for url, _timeout, headers in manager.session.calls] == [
        (META_URL, AUTH), (ARCHIVE_URL, AUTH)]
    assert _warnings(caplog) == []


def test_a_fetching_manager_means_wait_with_no_archive_request(manager, caplog):
    """BS-WK-5, BS-WK-10: a 202 ``queued`` answer to a waiting caller is WAIT; nothing else is asked."""
    manager.answer({META_URL: FakeResponse(202, {'status': 'queued'})})

    result = blender_from_manager.fetch(VERSION, PLATFORM, manager.dest, wait=True)

    assert result == ManagerResult(ManagerAnswer.WAIT, state='queued')
    assert [url for url, *_ in manager.session.calls] == [META_URL]
    assert _warnings(caplog) == []


def _meta(**changes):
    return FakeResponse(200, {**GOOD, **changes})


@pytest.mark.parametrize('answers, write_error, state', [
    ({META_URL: FakeResponse(404, {'status': 'failed'})}, None, 'failed'),
    ({META_URL: FakeResponse(404, {'status': 'not_wanted'})}, None, 'not_wanted'),
    ({META_URL: FakeResponse(404, b'<html>Not Found</html>')}, None, None),
    ({META_URL: requests.exceptions.ConnectionError('refused')}, None, None),
    ({META_URL: FakeResponse(202, {'status': 'fetching'})}, None, 'fetching'),
    ({META_URL: _meta(version='4.5.9')}, None, 'ready'),
    ({META_URL: _meta(file_name='blender-4.5.8-linux-x64.zip')}, None, 'ready'),
    ({META_URL: _meta(sha256='ab' * 31)}, None, 'ready'),
    ({META_URL: FakeResponse(200, GOOD), ARCHIVE_URL: FakeResponse(404, {'status': 'not_ready'})}, None, 'ready'),
    ({META_URL: FakeResponse(200, GOOD), ARCHIVE_URL: FakeResponse(200)}, SourceFailure('checksum mismatch'), 'ready'),
    ({META_URL: Redirect(302)}, None, None),
    ({META_URL: FakeResponse(200, GOOD), ARCHIVE_URL: Redirect(307)}, None, 'ready'),
], ids=['failed', 'not-wanted', 'plain-404', 'no-answer', 'still-fetching-not-waiting', 'other-version',
        'wrong-file-name', 'bad-sha256', 'archive-404-after-200', 'checksum-mismatch', 'metadata-redirect',
        'archive-redirect'])
def test_an_answer_the_worker_cannot_use_goes_online_at_once(manager, caplog, answers, write_error, state):
    """BS-WK-5, BS-WK-6: another status, no answer, a refused 200 (another version, a file name the worker
    did not build, a sha256 that is not 64 hex), an archive answer other than 200 after a 200, a checksum
    mismatch, or a 3xx to either request (not followed; its body is never read): one WARNING, UNAVAILABLE,
    every response closed, and no ``.part`` or archive left."""
    manager.answer(answers)
    manager.write_error = write_error

    result = blender_from_manager.fetch(VERSION, PLATFORM, manager.dest)

    assert result == ManagerResult(ManagerAnswer.UNAVAILABLE, state=state)
    assert len(_warnings(caplog)) == 1
    assert all(a.closed for a in answers.values() if isinstance(a, FakeResponse))
    assert not manager.dest.exists() or list(manager.dest.iterdir()) == []


@pytest.mark.parametrize('token, pinned', [({}, True), ({'Authorization': 'Token t0ken'}, False)],
                         ids=['not-enrolled', 'session-not-pinned'])
def test_the_token_is_sent_only_over_a_pinned_session(manager, monkeypatch, token, pinned):
    """BS-WK-5, Security: with no token, or a session that does not pin the manager's certificate, nothing
    is sent and the install goes online."""
    monkeypatch.setattr(blender_from_manager, 'get_auth_headers', lambda: token)
    manager.answer({}, pinned=pinned)

    result = blender_from_manager.fetch(VERSION, PLATFORM, manager.dest)

    assert result == ManagerResult(ManagerAnswer.UNAVAILABLE)
    assert manager.session.calls == []
