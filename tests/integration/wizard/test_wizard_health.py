# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Integration tests for the wizard ``GET /api/health/`` endpoint.

Spec: ``development/specs/wizard_health_endpoint.md`` (issue #160).

The launcher's cold-boot probe (introduced in #159) polls
``https://127.0.0.1:<port>/api/health/`` for up to 30 s. This test
spins up a real wizard subprocess, hits the endpoint over HTTPS, and
asserts the FR-W14 envelope shape (``{boot_id, version}``).
"""

from __future__ import annotations

import json

from . import _http


def test_health_endpoint_returns_envelope(wizard_process):
    """Live wizard subprocess: GET /api/health/ -> 200 + envelope."""
    url = f"{wizard_process.base_url}/api/health/"
    status, headers, body = _http.get(url)
    assert status == 200, body
    assert headers.get("Content-Type", "").startswith("application/json"), headers
    payload = json.loads(body.decode("utf-8"))
    assert "boot_id" in payload and isinstance(payload["boot_id"], str) and payload["boot_id"]
    assert "version" in payload and isinstance(payload["version"], str) and payload["version"]


def test_health_endpoint_no_auth_required(wizard_process):
    """The launcher's probe is anonymous — no headers required."""
    url = f"{wizard_process.base_url}/api/health/"
    status, _, body = _http.get(url, headers={})
    assert status == 200, body


def test_health_endpoint_boot_id_stable(wizard_process):
    """Two consecutive requests share one boot_id within a wizard process."""
    url = f"{wizard_process.base_url}/api/health/"
    _, _, body_a = _http.get(url)
    _, _, body_b = _http.get(url)
    a = json.loads(body_a.decode("utf-8"))["boot_id"]
    b = json.loads(body_b.decode("utf-8"))["boot_id"]
    assert a == b, (a, b)


def test_health_endpoint_post_returns_405(wizard_process):
    """Non-GET methods return 405 with ``Allow: GET``."""
    url = f"{wizard_process.base_url}/api/health/"
    status, headers, _ = _http.request("POST", url, body=b"", timeout=5.0)
    assert status == 405, status
    assert headers.get("Allow") == "GET", headers
