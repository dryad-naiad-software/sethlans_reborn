# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``web_ui/setup/handlers_password.py``.

Phase 4c of the Waitress migration: the handler is sync WSGI and
guarded by :func:`setup_mutation_lock`.  Tests drive the handler
directly via a WSGI ``environ`` + ``start_response`` capture (no
async plumbing).  Module state is reset by the autouse fixtures
in ``conftest.py``.
"""

import io
import json
import threading

from sethlans_worker_agent.web_ui.setup.handlers_password import (
    handle_set_worker_password,
    _MIN_PASSWORD_LENGTH,
)
from sethlans_worker_agent.web_ui.setup import lock as lock_module

from tests.unit.worker._wsgi_helpers import StartResponseCapture


def _make_environ(body: bytes) -> dict:
    return {
        'REQUEST_METHOD': 'POST',
        'PATH_INFO': '/api/setup/worker-password/',
        'CONTENT_LENGTH': str(len(body)),
        'wsgi.input': io.BytesIO(body),
    }


def _call(environ, cap):
    return b''.join(handle_set_worker_password(environ, cap))


def _status_code(cap: StartResponseCapture) -> int:
    return int((cap.status or '').split(' ', 1)[0])


class TestHandleSetWorkerPassword:
    def test_calls_set_password_on_valid_input(self, mocker):
        mock_set_pw = mocker.patch(
            "sethlans_worker_agent.web_ui.auth.set_password",
        )

        body = json.dumps({
            "password": "securepassword123",
        }).encode()
        cap = StartResponseCapture()
        out = _call(_make_environ(body), cap)

        assert _status_code(cap) == 200
        assert json.loads(out)["status"] == "ok"
        mock_set_pw.assert_called_once_with("securepassword123")

    def test_rejects_short_password(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.auth.set_password",
        )

        short_pw = "a" * (_MIN_PASSWORD_LENGTH - 1)
        body = json.dumps({"password": short_pw}).encode()
        cap = StartResponseCapture()
        out = _call(_make_environ(body), cap)

        assert _status_code(cap) == 400
        assert "at least" in json.loads(out)["error"].lower()

    def test_rejects_empty_password(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.auth.set_password",
        )

        body = json.dumps({"password": ""}).encode()
        cap = StartResponseCapture()
        _call(_make_environ(body), cap)

        assert _status_code(cap) == 400

    def test_rejects_missing_password_field(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.auth.set_password",
        )

        body = json.dumps({}).encode()
        cap = StartResponseCapture()
        _call(_make_environ(body), cap)

        assert _status_code(cap) == 400

    def test_appends_checkpoint(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.auth.set_password",
        )
        from sethlans_worker_agent.web_ui.setup.handlers_status import (
            get_current_checkpoints,
        )

        body = json.dumps({
            "password": "goodpassword",
        }).encode()
        cap = StartResponseCapture()
        _call(_make_environ(body), cap)

        assert "password_set" in get_current_checkpoints()

    def test_rejects_non_object_body(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.auth.set_password",
        )

        body = json.dumps([1, 2, 3]).encode()
        cap = StartResponseCapture()
        out = _call(_make_environ(body), cap)

        assert _status_code(cap) == 400
        assert "JSON object" in json.loads(out)["error"]

    def test_exactly_min_length_is_accepted(self, mocker):
        mock_set_pw = mocker.patch(
            "sethlans_worker_agent.web_ui.auth.set_password",
        )

        pw = "a" * _MIN_PASSWORD_LENGTH
        body = json.dumps({"password": pw}).encode()
        cap = StartResponseCapture()
        _call(_make_environ(body), cap)

        assert _status_code(cap) == 200
        mock_set_pw.assert_called_once_with(pw)

    def test_rejects_malformed_json(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.auth.set_password",
        )

        body = b'not-json{'
        cap = StartResponseCapture()
        out = _call(_make_environ(body), cap)

        assert _status_code(cap) == 400
        assert json.loads(out) == {"error": "Invalid JSON"}

    def test_body_over_cap_returns_413(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.auth.set_password",
        )

        body = b'x' * (4096 + 1)
        cap = StartResponseCapture()
        out = _call(_make_environ(body), cap)

        assert _status_code(cap) == 413
        assert json.loads(out) == {"error": "Request body too large"}

    def test_contention_returns_409(self, mocker):
        """Hold the lock in a background thread, then hit the handler.

        Uses a ``threading.Event`` pair for deterministic
        synchronization: the holder signals it has the lock, the
        main thread calls the handler (which must see it as taken),
        then the main thread releases the holder.
        """
        mocker.patch(
            "sethlans_worker_agent.web_ui.auth.set_password",
        )

        lock_held = threading.Event()
        release_holder = threading.Event()

        def holder():
            with lock_module.setup_mutation_lock() as acquired:
                assert acquired
                lock_held.set()
                # Keep the lock until the main thread tells us to let go.
                release_holder.wait(timeout=5)

        t = threading.Thread(target=holder)
        t.start()
        try:
            assert lock_held.wait(timeout=5)

            body = json.dumps({"password": "goodpassword"}).encode()
            cap = StartResponseCapture()
            out = _call(_make_environ(body), cap)

            assert _status_code(cap) == 409
            payload = json.loads(out)
            assert payload == {
                "error": "Setup mutation in progress; retry after "
                         "current operation completes.",
            }
        finally:
            release_holder.set()
            t.join(timeout=5)
