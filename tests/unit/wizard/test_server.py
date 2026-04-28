# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wizard/sethlans_wizard/server.py`` (Spec 1 / A3).

Covers FR-W3 (port resolution, env override), FR-W12 (Waitress
threads, single handoff-state lock invariant), and the ``create_app``
WSGI shape. The actual Waitress + TLS bind is exercised end-to-end in
the integration suite (Phase D) — these tests stay in-process.
"""

from __future__ import annotations

import io
import json

import pytest

from wizard.sethlans_wizard import auth_state, server


_SETUP_TOKEN = b"setup-token-xyz-99887766554433221100"
_IPC_SECRET = b"ipc-hmac-secret-bytes-zzz-yyy-xxx"


@pytest.fixture(autouse=True)
def _reset_auth_state():
    auth_state.reset_state_for_tests()
    yield
    auth_state.reset_state_for_tests()


# ---------------------------------------------------------------------
# resolve_port (FR-W3)
# ---------------------------------------------------------------------

class TestResolvePort:

    def test_default_when_env_unset(self):
        # Issue #170: wizard moved off the public TLS port (8100, now
        # Caddy's) onto a loopback range starting at 8099.
        assert server.resolve_port(env={}) == server.DEFAULT_WIZARD_PORT
        assert server.DEFAULT_WIZARD_PORT == 8099

    def test_default_when_env_empty_string(self):
        assert server.resolve_port(env={"SETHLANS_WIZARD_PORT": ""}) == 8099

    def test_env_override_honored(self):
        port = server.resolve_port(env={"SETHLANS_WIZARD_PORT": "8103"})
        assert port == 8103

    def test_env_override_must_be_int(self):
        with pytest.raises(ValueError, match="integer"):
            server.resolve_port(env={"SETHLANS_WIZARD_PORT": "not-a-port"})

    def test_env_override_must_be_in_range(self):
        with pytest.raises(ValueError, match="range"):
            server.resolve_port(env={"SETHLANS_WIZARD_PORT": "70000"})
        with pytest.raises(ValueError, match="range"):
            server.resolve_port(env={"SETHLANS_WIZARD_PORT": "0"})

    def test_scan_range_constants_match_spec(self):
        # Issue #170 FR-W3: 8099 + 8101..8104 (skipping 8100 which is
        # reserved for Caddy's public TLS bind).
        assert server.PORT_SCAN_RANGE == (8099, 8101, 8102, 8103, 8104)


class TestBindHost:

    def test_bind_host_is_loopback(self):
        # Issue #170 NFR-6 / AC-WizardLoopback: wizard binds 127.0.0.1
        # only — Caddy fronts public reachability.
        assert server.WIZARD_BIND_HOST == "127.0.0.1"


class TestNoTLSPlumbing:
    """Issue #170 AC-NoListenerTLS: no ssl/wrap_socket in the wizard."""

    def test_run_signature_has_no_cert_args(self):
        import inspect
        sig = inspect.signature(server.run)
        params = list(sig.parameters)
        # Exactly (app, host, port) — no cert_path/key_path leftovers.
        assert params == ["app", "host", "port"], params

    def test_server_module_does_not_import_ssl(self):
        import wizard.sethlans_wizard.server as srv
        assert not hasattr(srv, "ssl"), (
            "ssl module must not be imported in the wizard server"
        )
        # Source-level check: catches a future regression that imports
        # ssl lazily inside a function body too.
        from pathlib import Path
        source = Path(srv.__file__).read_text(encoding="utf-8")
        assert "import ssl" not in source
        assert "wrap_socket" not in source


# ---------------------------------------------------------------------
# create_app — guard rails + WSGI shape
# ---------------------------------------------------------------------

class TestCreateApp:

    def test_rejects_empty_setup_token(self, tmp_path):
        with pytest.raises(ValueError):
            server.create_app(tmp_path, b"", _IPC_SECRET)

    def test_rejects_empty_ipc_secret(self, tmp_path):
        with pytest.raises(ValueError):
            server.create_app(tmp_path, _SETUP_TOKEN, b"")

    def test_returns_callable(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        assert callable(app)

    def test_router_has_auth_route(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        prefixes = [r[0] for r in app._router._routes]  # type: ignore[attr-defined]
        assert "/api/wizard/auth/" in prefixes

    def test_router_has_health_route(self, tmp_path):
        """Issue #160: ``/api/health/`` is registered for the launcher probe."""
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        prefixes = [r[0] for r in app._router._routes]  # type: ignore[attr-defined]
        assert "/api/health/" in prefixes

    def test_health_route_registered_before_index(self, tmp_path):
        """AC-RouteOrdering: ``/api/health/`` precedes the ``/`` index mount."""
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        prefixes = [r[0] for r in app._router._routes]  # type: ignore[attr-defined]
        assert "/api/health/" in prefixes
        assert "/" in prefixes
        assert prefixes.index("/api/health/") < prefixes.index("/")

    def test_health_route_returns_envelope(self, tmp_path):
        """AC-EndpointExists via the live router (exact-match dispatch)."""
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        env = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/api/health/",
            "QUERY_STRING": "",
            "REMOTE_ADDR": "127.0.0.1",
            "CONTENT_LENGTH": "0",
            "wsgi.input": io.BytesIO(b""),
        }
        status, headers, body = _invoke(app, env)
        assert status.startswith("200"), status
        assert headers.get("Content-Type") == "application/json"
        payload = json.loads(body.decode("utf-8"))
        assert "boot_id" in payload and payload["boot_id"]
        assert "version" in payload and payload["version"]


# ---------------------------------------------------------------------
# WSGI dispatch behaviour
# ---------------------------------------------------------------------

def _invoke(app, environ):
    captured: dict = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    body = b"".join(app(environ, start_response))
    return captured.get("status"), captured.get("headers", {}), body


def _post_environ(path: str, body: bytes) -> dict:
    return {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "REMOTE_ADDR": "127.0.0.1",
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }


class TestDispatch:

    def test_unknown_path_returns_404(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        env = _post_environ("/no/such/route", b"{}")
        status, _, body = _invoke(app, env)
        assert status.startswith("404"), status
        assert b"Not Found" in body

    def test_auth_route_dispatches_to_handler(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        body = json.dumps({"token": _SETUP_TOKEN.decode("ascii")}).encode("utf-8")
        env = _post_environ("/api/wizard/auth/", body)
        status, _, resp_body = _invoke(app, env)
        assert status.startswith("200"), status
        payload = json.loads(resp_body.decode("utf-8"))
        assert payload["status"] == "ok"
        assert "session_token" in payload

    def test_auth_route_wrong_token_returns_403(self, tmp_path):
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        body = json.dumps({"token": "WRONG"}).encode("utf-8")
        env = _post_environ("/api/wizard/auth/", body)
        status, _, _ = _invoke(app, env)
        assert status.startswith("403")

    def test_auth_path_partial_does_not_match(self, tmp_path):
        """Router uses exact-equal path match, not prefix."""
        app = server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        env = _post_environ("/api/wizard/auth/extra", b"{}")
        status, _, _ = _invoke(app, env)
        assert status.startswith("404")


# ---------------------------------------------------------------------
# Router unit tests
# ---------------------------------------------------------------------

class TestRouter:

    def test_add_rejects_path_without_leading_slash(self):
        r = server.Router()
        with pytest.raises(ValueError):
            r.add("api/wizard/auth/", lambda e, s: [b""])

    def test_first_match_wins(self):
        r = server.Router()
        r.add("/x/", lambda e, s: (s("200 OK", [("Content-Length", "1")]), [b"a"])[1])
        r.add("/y/", lambda e, s: (s("200 OK", [("Content-Length", "1")]), [b"b"])[1])
        env = {"PATH_INFO": "/x/"}
        captured = {}

        def sr(status, headers):
            captured["status"] = status

        list(r.dispatch(env, sr))
        assert captured["status"] == "200 OK"

    def test_unknown_returns_404(self):
        r = server.Router()
        env = {"PATH_INFO": "/missing"}
        captured = {}

        def sr(status, headers):
            captured["status"] = status

        body = b"".join(r.dispatch(env, sr))
        assert captured["status"].startswith("404")
        assert b"Not Found" in body


# ---------------------------------------------------------------------
# FR-W12 single handoff-state lock invariant
# ---------------------------------------------------------------------

class TestHandoffStateLock:

    def test_create_app_does_not_introduce_a_second_lock(self, tmp_path):
        """The auth_state lock IS the only handoff lock per FR-W12."""
        before = auth_state.get_handoff_lock()
        server.create_app(tmp_path, _SETUP_TOKEN, _IPC_SECRET)
        after = auth_state.get_handoff_lock()
        assert before is after

    def test_waitress_threads_constant_matches_spec(self):
        # FR-W12 mandates threads=4.
        assert server.WAITRESS_THREADS == 4


# ---------------------------------------------------------------------
# get_server_ref — A4 will close() through this slot
# ---------------------------------------------------------------------

class TestServerRef:

    def test_singleton_returned(self):
        a = server.get_server_ref()
        b = server.get_server_ref()
        assert a is b

    def test_set_get_clear(self):
        ref = server.get_server_ref()
        marker = object()
        ref.set(marker)
        try:
            assert ref.get() is marker
        finally:
            ref.clear()
        assert ref.get() is None
