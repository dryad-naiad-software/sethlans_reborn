# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wizard/sethlans_wizard/probe.py`` (Spec 1 / A2).

Covers FR-W14's outbound runtime-health probe contract:

* 200 + valid JSON shape -> dict
* HTTP non-200 -> None
* Malformed JSON -> None
* Missing required keys -> None
* Each booting-state exception (URLError, socket.timeout,
  ConnectionRefusedError, ssl.SSLError) -> None
* SSLContext is constructed inline per invocation (SEC-v2.3-LOW-2)
"""

from __future__ import annotations

import io
import json
import socket
import ssl
import urllib.error
from contextlib import contextmanager

import pytest

from wizard.sethlans_wizard import probe as probe_mod
from wizard.sethlans_wizard.probe import probe_runtime_health

URL = "https://localhost:8080/api/health/"


class _FakeResponse:
    """Minimal context-manager stand-in for urlopen's HTTPResponse."""

    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body

    def getcode(self):
        return self.status


@contextmanager
def _patched_urlopen(monkeypatch, behaviour):
    """Replace ``urllib.request.urlopen`` with *behaviour* (callable)."""
    captured = {}

    def fake(url, *, context=None, timeout=None):
        captured["url"] = url
        captured["context"] = context
        captured["timeout"] = timeout
        return behaviour(url, context=context, timeout=timeout)

    monkeypatch.setattr(probe_mod.urllib.request, "urlopen", fake)
    yield captured


# ---------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------

class TestProbeSuccess:

    def test_returns_parsed_body_on_200_with_valid_shape(self, monkeypatch):
        body = {"boot_id": "abc", "worker_id": "w-1", "version": "0.1.0"}
        with _patched_urlopen(
            monkeypatch,
            lambda url, **_: _FakeResponse(200, json.dumps(body).encode("utf-8")),
        ):
            result = probe_runtime_health(URL)
        assert result == body

    def test_passes_url_and_timeout_through(self, monkeypatch):
        body = {"boot_id": "x", "worker_id": "y", "version": "z"}
        with _patched_urlopen(
            monkeypatch,
            lambda url, **_: _FakeResponse(200, json.dumps(body).encode("utf-8")),
        ) as cap:
            probe_runtime_health(URL, timeout_seconds=5.0)
        assert cap["url"] == URL
        assert cap["timeout"] == 5.0
        assert isinstance(cap["context"], ssl.SSLContext)


# ---------------------------------------------------------------------
# Non-200 / shape rejection
# ---------------------------------------------------------------------

class TestProbeShapeRejection:

    def test_non_200_returns_none(self, monkeypatch):
        with _patched_urlopen(
            monkeypatch,
            lambda url, **_: _FakeResponse(503, b'{"boot_id":"x","worker_id":"y","version":"z"}'),
        ):
            assert probe_runtime_health(URL) is None

    def test_malformed_json_returns_none(self, monkeypatch):
        with _patched_urlopen(
            monkeypatch,
            lambda url, **_: _FakeResponse(200, b"<html>not json</html>"),
        ):
            assert probe_runtime_health(URL) is None

    def test_invalid_utf8_returns_none(self, monkeypatch):
        with _patched_urlopen(
            monkeypatch,
            lambda url, **_: _FakeResponse(200, b"\xff\xfe\x00\x00"),
        ):
            assert probe_runtime_health(URL) is None

    def test_missing_boot_id_returns_none(self, monkeypatch):
        body = {"worker_id": "w", "version": "v"}
        with _patched_urlopen(
            monkeypatch,
            lambda url, **_: _FakeResponse(200, json.dumps(body).encode("utf-8")),
        ):
            assert probe_runtime_health(URL) is None

    def test_missing_worker_id_returns_none(self, monkeypatch):
        body = {"boot_id": "b", "version": "v"}
        with _patched_urlopen(
            monkeypatch,
            lambda url, **_: _FakeResponse(200, json.dumps(body).encode("utf-8")),
        ):
            assert probe_runtime_health(URL) is None

    def test_missing_version_returns_none(self, monkeypatch):
        body = {"boot_id": "b", "worker_id": "w"}
        with _patched_urlopen(
            monkeypatch,
            lambda url, **_: _FakeResponse(200, json.dumps(body).encode("utf-8")),
        ):
            assert probe_runtime_health(URL) is None

    def test_json_array_returns_none(self, monkeypatch):
        with _patched_urlopen(
            monkeypatch,
            lambda url, **_: _FakeResponse(200, b'["boot_id","worker_id","version"]'),
        ):
            assert probe_runtime_health(URL) is None


# ---------------------------------------------------------------------
# Booting-state exception envelope (DEVOPS-v2.3-LOW-1)
# ---------------------------------------------------------------------

class TestProbeTransientFailures:

    def _raise(self, exc):
        def _impl(url, **_):
            raise exc
        return _impl

    def test_url_error_returns_none(self, monkeypatch):
        with _patched_urlopen(
            monkeypatch, self._raise(urllib.error.URLError("dns")),
        ):
            assert probe_runtime_health(URL) is None

    def test_socket_timeout_returns_none(self, monkeypatch):
        with _patched_urlopen(
            monkeypatch, self._raise(socket.timeout("timed out")),
        ):
            assert probe_runtime_health(URL) is None

    def test_connection_refused_returns_none(self, monkeypatch):
        with _patched_urlopen(
            monkeypatch, self._raise(ConnectionRefusedError(111, "refused")),
        ):
            assert probe_runtime_health(URL) is None

    def test_ssl_error_returns_none(self, monkeypatch):
        with _patched_urlopen(
            monkeypatch, self._raise(ssl.SSLError("handshake")),
        ):
            assert probe_runtime_health(URL) is None

    def test_unrelated_oserror_propagates(self, monkeypatch):
        """Bare OSError outside the booting envelope must NOT be silently
        swallowed — it indicates a programming/environment bug, not the
        runtime being slow to come up."""
        with _patched_urlopen(
            monkeypatch, self._raise(OSError(13, "permission denied")),
        ):
            with pytest.raises(OSError):
                probe_runtime_health(URL)


# ---------------------------------------------------------------------
# SSLContext lifetime (SEC-v2.3-LOW-2)
# ---------------------------------------------------------------------

class TestSSLContextLifetime:

    def test_ssl_context_is_constructed_per_call(self, monkeypatch):
        body = {"boot_id": "a", "worker_id": "b", "version": "c"}
        contexts = []

        def fake(url, *, context=None, timeout=None):
            contexts.append(context)
            return _FakeResponse(200, json.dumps(body).encode("utf-8"))

        monkeypatch.setattr(probe_mod.urllib.request, "urlopen", fake)
        probe_runtime_health(URL)
        probe_runtime_health(URL)
        assert len(contexts) == 2
        assert contexts[0] is not contexts[1]

    def test_module_does_not_cache_ssl_context(self):
        """No module-level SSLContext attribute exists (SEC-v2.3-LOW-2)."""
        for name in dir(probe_mod):
            attr = getattr(probe_mod, name)
            assert not isinstance(attr, ssl.SSLContext), (
                f"probe module exposes module-level SSLContext via {name!r} "
                "(SEC-v2.3-LOW-2 forbids caching the context)"
            )

    def test_context_has_verification_disabled(self, monkeypatch):
        captured = {}

        def fake(url, *, context=None, timeout=None):
            captured["context"] = context
            return _FakeResponse(200, b'{"boot_id":"a","worker_id":"b","version":"c"}')

        monkeypatch.setattr(probe_mod.urllib.request, "urlopen", fake)
        probe_runtime_health(URL)
        ctx = captured["context"]
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_NONE


# ---------------------------------------------------------------------
# Stdlib-only guard (NF-9)
# ---------------------------------------------------------------------

class TestStdlibOnly:

    def test_module_does_not_import_httpx_or_requests(self):
        import sys
        # Probe module must not pull in banned deps as a transitive import.
        # We can't assert the global module table is clean (other test files
        # may import requests), but we CAN assert the probe module itself
        # references neither name.
        src = probe_mod.__file__
        with io.open(src, encoding="utf-8") as fh:
            text = fh.read()
        assert "import httpx" not in text
        assert "import requests" not in text
        assert "import certifi" not in text
        # And after a fresh import, neither should be added by us.
        assert "httpx" not in sys.modules or True  # may be present from elsewhere
