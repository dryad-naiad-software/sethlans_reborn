# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``handle_health_wsgi``
(``worker/sethlans_worker_agent/web_ui/runtime/handlers_health.py``).

Covers FR-H-2 through FR-H-7:

* Response status, ``Content-Type``, and exact body key set.
* ``boot_id`` mirrors ``runtime_state.worker_boot_id`` at request time.
* ``worker_id`` mirrors ``system_monitor.WORKER_ID`` (post-enrollment).
* ``worker_id`` is ``null`` (Python ``None``) when unset (FR-H-7).
* ``version`` is sourced from ``shared.version.get_version`` (verified
  by stubbing the lazy import target).
"""

from __future__ import annotations

import io
import json
from typing import List, Tuple

import pytest

from sethlans_worker_agent import runtime_state, system_monitor
from sethlans_worker_agent.web_ui.runtime import handlers_health


# --- Test helpers ------------------------------------------------------

class StartResponseRecorder:
    """Captures WSGI ``start_response`` invocations for assertion."""

    def __init__(self) -> None:
        self.status: str = ''
        self.headers: List[Tuple[str, str]] = []

    def __call__(
        self, status: str, headers: List[Tuple[str, str]], exc_info=None,
    ) -> None:
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


def _make_environ() -> dict:
    """Minimal WSGI environ for a ``GET /api/health/`` invocation."""
    return {
        'REQUEST_METHOD': 'GET',
        'PATH_INFO': '/api/health/',
        'QUERY_STRING': '',
        'SERVER_NAME': 'localhost',
        'SERVER_PORT': '8080',
        'wsgi.url_scheme': 'https',
        'wsgi.input': io.BytesIO(b''),
    }


def _drain(iterable) -> bytes:
    return b''.join(iterable)


# --- Fixtures ----------------------------------------------------------

@pytest.fixture
def reset_worker_id():
    """Snapshot/restore ``system_monitor.WORKER_ID`` around the test.

    Tests mutate this module-level attribute to drive the pre/post
    enrollment branches of the handler; the fixture guarantees we
    do not leak state into sibling tests.
    """
    original = system_monitor.WORKER_ID
    yield
    system_monitor.WORKER_ID = original


@pytest.fixture
def stub_version(mocker):
    """Stub ``shared.version.get_version`` via the lazy import path.

    The handler does ``from shared.version import get_version`` inside
    the function body (FR-H-5), so patching the attribute on the
    ``shared.version`` module is what the handler actually reads.
    """
    return mocker.patch(
        'shared.version.get_version', return_value='9.9.9-test',
    )


# --- Tests -------------------------------------------------------------

class TestHealthResponseShape:
    """FR-H-2: 200, Content-Type, exact-key body."""

    def test_returns_200(self, reset_worker_id, stub_version):
        rec = StartResponseRecorder()
        _drain(handlers_health.handle_health_wsgi(_make_environ(), rec))
        assert rec.status_code == 200

    def test_content_type_is_json(self, reset_worker_id, stub_version):
        rec = StartResponseRecorder()
        _drain(handlers_health.handle_health_wsgi(_make_environ(), rec))
        assert rec.header('Content-Type') == 'application/json'

    def test_content_length_matches_body(
        self, reset_worker_id, stub_version,
    ):
        rec = StartResponseRecorder()
        body = _drain(
            handlers_health.handle_health_wsgi(_make_environ(), rec),
        )
        assert rec.header('Content-Length') == str(len(body))

    def test_body_keys_are_exactly_boot_id_worker_id_version(
        self, reset_worker_id, stub_version,
    ):
        rec = StartResponseRecorder()
        body = _drain(
            handlers_health.handle_health_wsgi(_make_environ(), rec),
        )
        payload = json.loads(body)
        assert set(payload.keys()) == {'boot_id', 'worker_id', 'version'}


class TestHealthBootId:
    """FR-H-3: ``boot_id`` is read from ``runtime_state``."""

    def test_boot_id_matches_runtime_state(
        self, reset_worker_id, stub_version,
    ):
        rec = StartResponseRecorder()
        body = _drain(
            handlers_health.handle_health_wsgi(_make_environ(), rec),
        )
        payload = json.loads(body)
        assert payload['boot_id'] == runtime_state.worker_boot_id

    def test_boot_id_reflects_runtime_state_changes(
        self, reset_worker_id, stub_version, mocker,
    ):
        # Read at request time -- if a future refactor caches
        # worker_boot_id at handler-import time, this test catches it.
        mocker.patch.object(
            runtime_state, 'worker_boot_id', 'deadbeef' * 4,
        )
        rec = StartResponseRecorder()
        body = _drain(
            handlers_health.handle_health_wsgi(_make_environ(), rec),
        )
        payload = json.loads(body)
        assert payload['boot_id'] == 'deadbeef' * 4


class TestHealthWorkerId:
    """FR-H-4 / FR-H-7: ``worker_id`` mirrors ``system_monitor.WORKER_ID``."""

    def test_worker_id_is_null_when_unset(
        self, reset_worker_id, stub_version,
    ):
        # Pre-enrollment path -- WORKER_ID has not been written yet.
        system_monitor.WORKER_ID = None
        rec = StartResponseRecorder()
        body = _drain(
            handlers_health.handle_health_wsgi(_make_environ(), rec),
        )
        payload = json.loads(body)
        # FR-H-7: 200 with worker_id JSON null even pre-enrollment.
        assert rec.status_code == 200
        assert payload['worker_id'] is None
        # Sanity: the JSON wire form really is `null`, not the string.
        assert b'"worker_id": null' in body

    def test_worker_id_mirrors_set_value(
        self, reset_worker_id, stub_version,
    ):
        # Post-enrollment path -- register_with_manager has populated
        # the module-global WORKER_ID with a UUID-like string.
        system_monitor.WORKER_ID = 'abc-123-worker-uuid'
        rec = StartResponseRecorder()
        body = _drain(
            handlers_health.handle_health_wsgi(_make_environ(), rec),
        )
        payload = json.loads(body)
        assert rec.status_code == 200
        assert payload['worker_id'] == 'abc-123-worker-uuid'


class TestHealthVersion:
    """FR-H-5: ``version`` comes from ``shared.version.get_version``."""

    def test_version_reflects_get_version_return(
        self, reset_worker_id, stub_version,
    ):
        rec = StartResponseRecorder()
        body = _drain(
            handlers_health.handle_health_wsgi(_make_environ(), rec),
        )
        payload = json.loads(body)
        assert payload['version'] == '9.9.9-test'

    def test_get_version_is_called(
        self, reset_worker_id, stub_version,
    ):
        rec = StartResponseRecorder()
        _drain(handlers_health.handle_health_wsgi(_make_environ(), rec))
        # Stub records that the lazy import path was actually used.
        assert stub_version.call_count >= 1
