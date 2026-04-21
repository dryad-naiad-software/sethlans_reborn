# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``web_ui/setup/handlers_enrollment.py`` -- happy path
and request-validation coverage.

Phase 4g of the Waitress migration: ``handle_enroll`` is sync WSGI.
Tests drive the handler with a raw WSGI ``environ`` + capturing
``start_response``; no asyncio plumbing.  ``enrollment_client.enroll``
and ``config_store.set_many`` are mocked so the tests never touch the
network or write real config files.

Companion file ``test_setup_handlers_enrollment_errors.py`` covers
the ``enrollment_client`` error -> HTTP status mapping, the
``setup_mutation_lock`` 409 contention path, and the FR-6 timeout
invariant on the outbound HTTP call.

Module state (selected manager, wizard checkpoints) is reset by the
autouse fixtures in ``conftest.py``.
"""

import io
import json

from sethlans_worker_agent.web_ui.setup import handlers_discovery
from sethlans_worker_agent.web_ui.setup.handlers_enrollment import (
    handle_enroll,
)

from tests.unit.worker._wsgi_helpers import StartResponseCapture


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _make_environ(body: bytes = b'') -> dict:
    env = {
        'REQUEST_METHOD': 'POST',
        'PATH_INFO': '/api/setup/worker/enroll/',
        'wsgi.input': io.BytesIO(body),
    }
    if body:
        env['CONTENT_LENGTH'] = str(len(body))
    return env


def _call(handler, environ, cap):
    return b''.join(handler(environ, cap))


def _status_code(cap: StartResponseCapture) -> int:
    return int((cap.status or '').split(' ', 1)[0])


def _set_selected_manager(
    url: str = "https://10.0.0.1:8080/api/",
    manager_id: str = "mgr-id-42",
    meta: dict | None = None,
) -> None:
    """Pre-populate the selected-manager module state.

    Writes directly to the module globals rather than routing through
    ``handle_select_manager`` so tests exercise ``handle_enroll`` in
    isolation.  The autouse ``_reset_setup_discovery_state`` fixture
    in ``conftest.py`` wipes this between tests.
    """
    if meta is None:
        meta = {
            "name": "Test Manager",
            "ip": "10.0.0.1",
            "host": "lab.example",
            "port": 8080,
        }
    handlers_discovery._selected_manager_url = url
    handlers_discovery._selected_manager_id = manager_id
    handlers_discovery._selected_manager_meta = meta


# -------------------------------------------------------------------
# Happy path
# -------------------------------------------------------------------


class TestHandleEnrollHappyPath:
    def test_success_returns_ok_and_manager_name(self, mocker):
        _set_selected_manager()
        mocker.patch(
            "sethlans_worker_agent.enrollment_client.enroll",
            return_value={
                "api_token": "tok-123",
                "cert_fingerprint": "AA:BB:CC",
                "manager_id": "mgr-id-42",
            },
        )
        mock_set_many = mocker.patch(
            "sethlans_worker_agent.config_store.set_many",
        )

        body = json.dumps({
            "enrollment_key": "4F9XK2PBQ7M3N8RT",
        }).encode()
        cap = StartResponseCapture()
        out = _call(handle_enroll, _make_environ(body), cap)

        assert _status_code(cap) == 200
        payload = json.loads(out)
        assert payload["status"] == "ok"
        assert payload["manager_name"] == "Test Manager"

        # set_many was called with the expected (key, value) pairs.
        mock_set_many.assert_called_once()
        pairs = dict(mock_set_many.call_args.args[0])
        assert pairs["manager.api_token"] == "tok-123"
        assert pairs["manager.cert_fingerprint"] == "AA:BB:CC"
        assert pairs["manager.manager_id"] == "mgr-id-42"
        assert pairs["manager.host"] == "10.0.0.1"
        assert pairs["manager.port"] == 8080

    def test_appends_enrolled_checkpoint_on_success(self, mocker):
        _set_selected_manager()
        from sethlans_worker_agent.web_ui.setup.handlers_status import (
            get_current_checkpoints,
        )

        mocker.patch(
            "sethlans_worker_agent.enrollment_client.enroll",
            return_value={
                "api_token": "t",
                "cert_fingerprint": "fp",
                "manager_id": "mid",
            },
        )
        mocker.patch(
            "sethlans_worker_agent.config_store.set_many",
        )

        body = json.dumps({
            "enrollment_key": "4F9XK2PBQ7M3N8RT",
        }).encode()
        cap = StartResponseCapture()
        _call(handle_enroll, _make_environ(body), cap)

        assert _status_code(cap) == 200
        assert "enrolled" in get_current_checkpoints()

    def test_manager_name_falls_back_when_meta_missing_name(
        self, mocker,
    ):
        _set_selected_manager(meta={"ip": "10.0.0.1", "port": 8080})
        mocker.patch(
            "sethlans_worker_agent.enrollment_client.enroll",
            return_value={
                "api_token": "t",
                "cert_fingerprint": "fp",
                "manager_id": "mid",
            },
        )
        mocker.patch(
            "sethlans_worker_agent.config_store.set_many",
        )

        body = json.dumps({
            "enrollment_key": "4F9XK2PBQ7M3N8RT",
        }).encode()
        cap = StartResponseCapture()
        out = _call(handle_enroll, _make_environ(body), cap)

        assert _status_code(cap) == 200
        assert json.loads(out)["manager_name"] == "Manager"


# -------------------------------------------------------------------
# Request validation
# -------------------------------------------------------------------


class TestHandleEnrollValidation:
    def test_returns_400_when_no_manager_selected(self):
        body = json.dumps({
            "enrollment_key": "4F9XK2PBQ7M3N8RT",
        }).encode()
        cap = StartResponseCapture()
        out = _call(handle_enroll, _make_environ(body), cap)

        assert _status_code(cap) == 400
        assert "No manager selected" in json.loads(out)["error"]

    def test_returns_400_when_key_empty(self):
        _set_selected_manager()
        body = json.dumps({"enrollment_key": ""}).encode()
        cap = StartResponseCapture()
        out = _call(handle_enroll, _make_environ(body), cap)

        assert _status_code(cap) == 400
        assert "required" in json.loads(out)["error"].lower()

    def test_returns_400_when_key_missing(self):
        _set_selected_manager()
        body = json.dumps({}).encode()
        cap = StartResponseCapture()
        _call(handle_enroll, _make_environ(body), cap)

        assert _status_code(cap) == 400

    def test_returns_400_for_non_object_body(self):
        _set_selected_manager()
        body = json.dumps("not-an-object").encode()
        cap = StartResponseCapture()
        out = _call(handle_enroll, _make_environ(body), cap)

        assert _status_code(cap) == 400
        assert "JSON object" in json.loads(out)["error"]

    def test_returns_400_for_malformed_json(self):
        _set_selected_manager()
        body = b'{not valid json'
        cap = StartResponseCapture()
        out = _call(handle_enroll, _make_environ(body), cap)

        assert _status_code(cap) == 400
        assert json.loads(out) == {"error": "Invalid JSON"}

    def test_returns_413_for_oversize_body(self):
        _set_selected_manager()
        body = b'x' * (4096 + 1)
        cap = StartResponseCapture()
        out = _call(handle_enroll, _make_environ(body), cap)

        assert _status_code(cap) == 413
        assert json.loads(out) == {"error": "Request body too large"}
