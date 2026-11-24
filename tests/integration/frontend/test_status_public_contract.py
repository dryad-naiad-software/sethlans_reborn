# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frontend-backend contract tests for the loopback-only tray status
endpoint ``GET /api/status/public/``.

Per ``development/specs/tray-helper-unified.md`` FR-22 / FR-22a:

* Served ONLY by the internal-origin Waitress loopback listener,
  which pins ``request.urlconf`` to
  ``sethlans_manager.urls_loopback`` and is bound to
  ``127.0.0.1:<loopback_port>``.
* Response shape has EXACTLY these keys:
  ``boot_id``, ``version``, ``workers_online``, ``jobs_queued``,
  ``jobs_rendering``.  No extras (``setup_mode`` was dropped with the
  manager setup-complete gate, issue #304).
* The MAIN 0.0.0.0 listener (``sethlans_manager.urls``) must NOT
  register the path — requests there must 404.

Since the frontend today never consumes this payload (it's pulled by
the Python tray), there is no TypeScript model to verify against.  The
contract here is Python-only but is tagged *frontend-facing* because
the tray counts in this payload are the SAME counts the dashboard
surfaces, so shape drift here will ship a UI bug eventually.
"""

from __future__ import annotations

import re
import uuid

import pytest
from django.test import Client, override_settings
from rest_framework.test import APIClient

from sethlans_manager import __version__ as MANAGER_VERSION

pytestmark = [
    pytest.mark.django_db,
]

# The internal-origin Waitress loopback listener pins the urls_loopback
# urlconf on its own socket at runtime. In pytest we swap ROOT_URLCONF
# per-test to exercise the same view-level contract.
_loopback_urlconf = override_settings(
    ROOT_URLCONF="sethlans_manager.urls_loopback",
)


EXPECTED_KEYS = frozenset({
    "boot_id",
    "version",
    "workers_online",
    "jobs_queued",
    "jobs_rendering",
})


def _get_via_loopback_urlconf():
    """Invoke the loopback urlconf directly via Django's test Client.

    The loopback urlconf is the same Python process as the main
    listener but is served by a separate Waitress loopback listener
    at runtime. We ``override_settings(ROOT_URLCONF=...)`` to route
    the test request through ``sethlans_manager.urls_loopback`` —
    close enough to verify view-level shape contracts.
    """
    with _loopback_urlconf:
        return Client().get("/api/status/public/")


class TestStatusPublicShape:
    """Shape contract — keys are exactly EXPECTED_KEYS, no extras."""

    def test_keys_are_exactly_the_spec_set(self):
        resp = _get_via_loopback_urlconf()
        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) == EXPECTED_KEYS, (
            f"Status-public shape drift. "
            f"Missing: {EXPECTED_KEYS - set(body.keys())}; "
            f"Extra: {set(body.keys()) - EXPECTED_KEYS}"
        )

    def test_no_redundant_state_field(self):
        """The v2 revision dropped ``state``; it must not come back."""
        body = _get_via_loopback_urlconf().json()
        assert "state" not in body, (
            "'state' was explicitly dropped in the v2 revision. "
            "Did a regression re-add it?"
        )


class TestStatusPublicBootId:

    def test_boot_id_is_nonempty_string(self):
        body = _get_via_loopback_urlconf().json()
        assert isinstance(body["boot_id"], str)
        assert body["boot_id"]

    def test_boot_id_is_uuid_hex(self):
        body = _get_via_loopback_urlconf().json()
        # runtime_state.manager_boot_id is ``uuid.uuid4().hex`` —
        # 32 lowercase hex chars, no dashes.
        assert re.fullmatch(
            r"[0-9a-f]{32}", body["boot_id"],
        ), f"boot_id must be a 32-char hex UUID, got {body['boot_id']!r}"
        # Parseable via uuid.UUID for belt-and-suspenders.
        uuid.UUID(hex=body["boot_id"])

    def test_boot_id_stable_within_process(self):
        a = _get_via_loopback_urlconf().json()["boot_id"]
        b = _get_via_loopback_urlconf().json()["boot_id"]
        assert a == b


class TestStatusPublicVersion:

    def test_version_matches_package_version(self):
        body = _get_via_loopback_urlconf().json()
        assert body["version"] == MANAGER_VERSION, (
            f"Version drift: payload={body['version']!r} "
            f"package={MANAGER_VERSION!r}"
        )


class TestStatusPublicCounts:

    def test_counts_are_non_negative_integers(self):
        body = _get_via_loopback_urlconf().json()
        for key in ("workers_online", "jobs_queued", "jobs_rendering"):
            val = body[key]
            assert isinstance(val, int) and not isinstance(val, bool), (
                f"{key} must be int; got {type(val).__name__}"
            )
            assert val >= 0, f"{key} must be >= 0; got {val}"


class TestStatusPublicMainListener404:
    """The main listener (0.0.0.0, ``sethlans_manager.urls``) MUST NOT
    expose this path.  Per FR-22, the loopback binding IS the
    authorization gate — a registered-but-reject variant would be
    defense-in-depth only at the Python layer.
    """

    def test_main_urlconf_returns_404(self):
        """Default ROOT_URLCONF is ``sethlans_manager.urls``."""
        resp = APIClient().get("/api/status/public/")
        assert resp.status_code == 404, (
            f"/api/status/public/ must not be reachable via the main "
            f"listener; got {resp.status_code}. The path must be "
            f"registered only in sethlans_manager.urls_loopback."
        )

# Note: status_public response is consumed only by the Python tray
# today; no TypeScript model exists for it. If the frontend ever grows
# a tray-dashboard widget that hits this payload, add a TS interface
# parity check here.
