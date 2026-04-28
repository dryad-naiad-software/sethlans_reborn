# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wizard/sethlans_wizard/handlers/health.py``.

Spec: ``development/specs/wizard_health_endpoint.md`` (issue #160).

Covers AC-EndpointExists, AC-EnvelopeShape, AC-BootIdStable,
AC-BootIdUniquePerProcess, AC-VersionFromShared, AC-MethodGuard,
AC-NoAuth, plus content-type and content-length sanity checks.

Route ordering (AC-RouteOrdering) and the smoke regression assertion
(AC-SmokeCatchesRegression) are exercised in ``test_server.py`` and
``tools/wizard_smoke.py`` respectively.
"""

from __future__ import annotations

import io
import json
import re

import pytest

from shared import version as shared_version
from wizard.sethlans_wizard.handlers.health import make_health_handler


_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _build_environ(*, method="GET", path="/api/health/",
                   remote_addr="127.0.0.1", query_string="", headers=None):
    env = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query_string,
        "REMOTE_ADDR": remote_addr,
        "CONTENT_LENGTH": "0",
        "wsgi.input": io.BytesIO(b""),
    }
    for hk, hv in (headers or {}).items():
        env["HTTP_" + hk.upper().replace("-", "_")] = hv
    return env


def _call(handler, environ):
    captured: dict = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    body = b"".join(handler(environ, start_response))
    return captured["status"], dict(captured["headers"]), body


@pytest.fixture(autouse=True)
def _stable_version(monkeypatch):
    """Pin the version so tests don't depend on the on-disk VERSION file.

    ``shared.version.get_version`` caches its first read in a module
    global. To keep this test self-contained, monkeypatch the function
    itself for every test in this file.
    """
    monkeypatch.setattr(shared_version, "get_version", lambda: "9.9.9-test")
    yield


# ---------------------------------------------------------------------
# Happy path / envelope shape
# ---------------------------------------------------------------------


def test_health_returns_200_with_envelope():
    """AC-EndpointExists + AC-EnvelopeShape."""
    handler = make_health_handler()
    status, headers, body = _call(handler, _build_environ())
    assert status.startswith("200"), status
    payload = json.loads(body.decode("utf-8"))
    assert set(payload.keys()) == {"boot_id", "version"}
    assert isinstance(payload["boot_id"], str) and payload["boot_id"]
    assert isinstance(payload["version"], str) and payload["version"]
    # boot_id is a UUID4 (hex with version-4 marker); fail loud if not.
    assert _UUID4_RE.match(payload["boot_id"]), payload["boot_id"]


def test_boot_id_stable_within_handler_lifetime():
    """AC-BootIdStable: two calls on the same handler -> same boot_id."""
    handler = make_health_handler()
    _, _, body_a = _call(handler, _build_environ())
    _, _, body_b = _call(handler, _build_environ())
    a = json.loads(body_a.decode("utf-8"))["boot_id"]
    b = json.loads(body_b.decode("utf-8"))["boot_id"]
    assert a == b, (a, b)


def test_boot_id_unique_across_handlers():
    """AC-BootIdUniquePerProcess: two handlers -> two boot_ids."""
    h1 = make_health_handler()
    h2 = make_health_handler()
    _, _, b1 = _call(h1, _build_environ())
    _, _, b2 = _call(h2, _build_environ())
    id1 = json.loads(b1.decode("utf-8"))["boot_id"]
    id2 = json.loads(b2.decode("utf-8"))["boot_id"]
    assert id1 != id2, (id1, id2)
    assert _UUID4_RE.match(id1)
    assert _UUID4_RE.match(id2)


def test_version_matches_shared(monkeypatch):
    """AC-VersionFromShared: response version == shared.version.get_version()."""
    monkeypatch.setattr(shared_version, "get_version", lambda: "1.2.3-from-shared")
    handler = make_health_handler()
    _, _, body = _call(handler, _build_environ())
    payload = json.loads(body.decode("utf-8"))
    assert payload["version"] == "1.2.3-from-shared"


# ---------------------------------------------------------------------
# Method guard / auth posture
# ---------------------------------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_non_get_returns_405_with_allow_get(method):
    """AC-MethodGuard: POST/PUT/DELETE -> 405 with Allow: GET."""
    handler = make_health_handler()
    status, headers, _ = _call(handler, _build_environ(method=method))
    assert status.startswith("405"), status
    assert headers.get("Allow") == "GET", headers


def test_no_auth_required():
    """AC-NoAuth: no Authorization, no Cookie, no session header -> 200."""
    handler = make_health_handler()
    # _build_environ omits all auth-flavored headers by default; assert
    # nothing of the sort leaked into the environ in case the helper
    # changes later.
    env = _build_environ()
    for key in env:
        assert "AUTHORIZATION" not in key
        assert "COOKIE" not in key
        assert "X_WIZARD_SESSION" not in key
    status, _, _ = _call(handler, env)
    assert status.startswith("200"), status


# ---------------------------------------------------------------------
# Content-Type / Content-Length
# ---------------------------------------------------------------------


def test_response_content_type_json():
    """Response is application/json (NFR-5)."""
    handler = make_health_handler()
    _, headers, _ = _call(handler, _build_environ())
    assert headers.get("Content-Type") == "application/json", headers


def test_response_content_length_correct():
    """Content-Length matches the body length exactly."""
    handler = make_health_handler()
    _, headers, body = _call(handler, _build_environ())
    assert headers.get("Content-Length") == str(len(body)), (
        headers.get("Content-Length"), len(body),
    )
