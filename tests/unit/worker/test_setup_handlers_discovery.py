# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``web_ui/setup/handlers_discovery.py``.

Phase 4f of the Waitress migration: both handlers are sync WSGI.
Tests drive them directly via a WSGI ``environ`` + ``start_response``
capture (no async plumbing).  The companion file
``test_setup_handlers_discovery_concurrency.py`` exercises the
``_state_lock`` atomicity, the ``setup_mutation_lock`` 409 semantics,
and the lock-free read path for GET /discover/.

Module state is reset by the autouse fixture
``_reset_setup_discovery_state`` in ``conftest.py``.
"""

import io
import json

from sethlans_worker_agent.web_ui.setup.handlers_discovery import (
    handle_discover,
    handle_select_manager,
    get_selected_manager_url,
    get_selected_manager_id,
    get_selected_manager_meta,
)

from tests.unit.worker._wsgi_helpers import StartResponseCapture


def _make_environ(method: str, path: str, body: bytes = b'') -> dict:
    env = {
        'REQUEST_METHOD': method,
        'PATH_INFO': path,
        'wsgi.input': io.BytesIO(body),
    }
    if body:
        env['CONTENT_LENGTH'] = str(len(body))
    return env


def _call(handler, environ, cap):
    return b''.join(handler(environ, cap))


def _status_code(cap: StartResponseCapture) -> int:
    return int((cap.status or '').split(' ', 1)[0])


# -------------------------------------------------------------------
# handle_discover
# -------------------------------------------------------------------

class TestHandleDiscover:
    def test_returns_managers_list(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_discovery"
            "._run_discovery",
            return_value=[
                {
                    "name": "Lab Manager",
                    "host": "lab.example",
                    "ip": "10.0.0.1",
                    "port": 8080,
                    "manager_id":
                        "00000000-0000-0000-0000-000000000042",
                    "version": "0.1.0",
                },
            ],
        )

        cap = StartResponseCapture()
        out = _call(
            handle_discover,
            _make_environ('GET', '/api/setup/discover/'),
            cap,
        )

        assert _status_code(cap) == 200
        body = json.loads(out)
        assert len(body["managers"]) == 1
        assert body["managers"][0]["name"] == "Lab Manager"

    def test_returns_empty_list_when_none_found(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_discovery"
            "._run_discovery",
            return_value=[],
        )

        cap = StartResponseCapture()
        out = _call(
            handle_discover,
            _make_environ('GET', '/api/setup/discover/'),
            cap,
        )

        assert _status_code(cap) == 200
        assert json.loads(out)["managers"] == []

    def test_multicast_listener_timeout_is_5_seconds(self, mocker):
        """FR-7 invariant: MulticastListener(timeout=5.0).

        The sync rewrite inlined the executor call; the 5-second
        bound on listener.discover() is the ONLY thing keeping the
        Waitress request thread from hanging indefinitely.  Assert
        it as a load-bearing invariant so a future refactor can't
        silently widen the window.
        """
        mock_listener_cls = mocker.patch(
            "sethlans_worker_agent.multicast_listener"
            ".MulticastListener",
        )
        mock_listener_cls.return_value.discover.return_value = {}

        cap = StartResponseCapture()
        _call(
            handle_discover,
            _make_environ('GET', '/api/setup/discover/'),
            cap,
        )

        # The handler must construct MulticastListener with the
        # 5.0-second timeout exactly -- FR-7 preservation.
        mock_listener_cls.assert_called_once_with(timeout=5.0)


# -------------------------------------------------------------------
# handle_select_manager -- request shape + validation
# -------------------------------------------------------------------

class TestHandleSelectManager:
    def test_stores_manager_url_and_id(self):
        body = json.dumps({
            "ip": "10.0.0.1",
            "host": "lab.example",
            "port": 8080,
            "manager_id": "test-manager-id",
        }).encode()
        cap = StartResponseCapture()
        out = _call(
            handle_select_manager,
            _make_environ(
                'POST', '/api/setup/worker/select-manager/', body,
            ),
            cap,
        )

        assert _status_code(cap) == 200
        resp = json.loads(out)
        assert resp["status"] == "ok"
        assert "10.0.0.1" in resp["manager_url"]
        assert resp["manager_id"] == "test-manager-id"
        assert get_selected_manager_url() == resp["manager_url"]
        assert get_selected_manager_id() == "test-manager-id"

    def test_rejects_missing_url_and_host(self):
        body = json.dumps({
            "port": 8080,
            "manager_id": "test-id",
        }).encode()
        cap = StartResponseCapture()
        out = _call(
            handle_select_manager,
            _make_environ(
                'POST', '/api/setup/worker/select-manager/', body,
            ),
            cap,
        )

        assert _status_code(cap) == 400
        err = json.loads(out)["error"]
        assert "manager_url" in err or "ip" in err

    def test_accepts_host_without_ip(self):
        body = json.dumps({
            "host": "lab.example",
            "port": 8080,
        }).encode()
        cap = StartResponseCapture()
        _call(
            handle_select_manager,
            _make_environ(
                'POST', '/api/setup/worker/select-manager/', body,
            ),
            cap,
        )

        assert _status_code(cap) == 200
        assert "lab.example" in get_selected_manager_url()

    def test_accepts_ip_without_host(self):
        body = json.dumps({
            "ip": "192.168.1.100",
            "port": 9090,
        }).encode()
        cap = StartResponseCapture()
        _call(
            handle_select_manager,
            _make_environ(
                'POST', '/api/setup/worker/select-manager/', body,
            ),
            cap,
        )

        assert _status_code(cap) == 200
        assert "192.168.1.100" in get_selected_manager_url()

    def test_accepts_manager_url_directly(self):
        body = json.dumps({
            "manager_url": "https://10.0.0.1:8080/api/",
            "manager_id": "mid-123",
        }).encode()
        cap = StartResponseCapture()
        _call(
            handle_select_manager,
            _make_environ(
                'POST', '/api/setup/worker/select-manager/', body,
            ),
            cap,
        )

        assert _status_code(cap) == 200
        assert (
            get_selected_manager_url() == "https://10.0.0.1:8080/api/"
        )
        assert get_selected_manager_id() == "mid-123"

    def test_rejects_non_object_body(self):
        body = json.dumps("not-object").encode()
        cap = StartResponseCapture()
        out = _call(
            handle_select_manager,
            _make_environ(
                'POST', '/api/setup/worker/select-manager/', body,
            ),
            cap,
        )

        assert _status_code(cap) == 400
        assert "JSON object" in json.loads(out)["error"]

    def test_rejects_oversize_body(self):
        # 4 KB cap enforced via parse_json_body_wsgi.
        body = b'x' * (4096 + 1)
        cap = StartResponseCapture()
        out = _call(
            handle_select_manager,
            _make_environ(
                'POST', '/api/setup/worker/select-manager/', body,
            ),
            cap,
        )

        assert _status_code(cap) == 413
        assert json.loads(out) == {"error": "Request body too large"}

    def test_appends_checkpoint(self):
        from sethlans_worker_agent.web_ui.setup.handlers_status \
            import get_current_checkpoints
        body = json.dumps({
            "ip": "10.0.0.1",
            "port": 8080,
        }).encode()
        cap = StartResponseCapture()
        _call(
            handle_select_manager,
            _make_environ(
                'POST', '/api/setup/worker/select-manager/', body,
            ),
            cap,
        )

        assert "manager_selected" in get_current_checkpoints()

    def test_get_meta_returns_defensive_copy(self):
        body = json.dumps({
            "manager_url": "https://10.0.0.1:8080/api/",
            "manager_id": "mid-123",
            "extra": "payload",
        }).encode()
        cap = StartResponseCapture()
        _call(
            handle_select_manager,
            _make_environ(
                'POST', '/api/setup/worker/select-manager/', body,
            ),
            cap,
        )

        meta = get_selected_manager_meta()
        assert meta is not None
        # Mutating the returned dict must NOT affect module state.
        meta["extra"] = "hijacked"
        fresh = get_selected_manager_meta()
        assert fresh["extra"] == "payload"
