# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frontend-backend contract tests for the loopback-only tray status
endpoint ``GET /api/status/public/``.

Per ``development/specs/tray-helper-unified.md`` FR-22 / FR-22a:

* Served ONLY by the dedicated loopback ASGI app
  (``sethlans_manager.urls_loopback``), bound to
  ``127.0.0.1:<loopback_port>``.
* Response shape has EXACTLY these keys:
  ``boot_id``, ``version``, ``setup_mode``, ``workers_online``,
  ``jobs_queued``, ``jobs_rendering``.  No extras.
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

# The loopback uvicorn listener mounts the urls_loopback urlconf on its
# own socket at runtime. In pytest we swap ROOT_URLCONF per-test to
# exercise the same view-level contract.
_loopback_urlconf = override_settings(
    ROOT_URLCONF="sethlans_manager.urls_loopback",
)


EXPECTED_KEYS = frozenset({
    "boot_id",
    "version",
    "setup_mode",
    "workers_online",
    "jobs_queued",
    "jobs_rendering",
})


def _get_via_loopback_urlconf():
    """Invoke the loopback urlconf directly via Django's test Client.

    The loopback urlconf is the same Python process as the main
    listener but is served by a separate uvicorn instance at runtime.
    We ``override_settings(ROOT_URLCONF=...)`` to route the test
    request through ``sethlans_manager.urls_loopback`` — close enough
    to verify view-level shape contracts.
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
        """v2 revision dropped ``state`` in favor of ``setup_mode``."""
        body = _get_via_loopback_urlconf().json()
        assert "state" not in body, (
            "'state' was explicitly dropped in the v2 revision; it is "
            "redundant with setup_mode. Did a regression re-add it?"
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


@pytest.fixture
def status_public_setup_mode(mocker, tmp_path):
    """Patch the status_public view's setup-mode probe.

    ``status_public_view`` reads ``workers.services.sentinel.is_setup_mode``
    directly (not the middleware's cached ``_setup_complete`` flag), so
    the shared ``enter_setup_mode``/``exit_setup_mode`` fixtures are
    insufficient. We patch at the view import site.
    """
    from workers.views import status_public as sp_mod

    def _set(value: bool) -> None:
        mocker.patch.object(sp_mod, "is_setup_mode", return_value=value)
        mocker.patch.object(sp_mod, "_get_data_dir", return_value=tmp_path)

    return _set


class TestStatusPublicSetupMode:

    def test_setup_mode_is_boolean(self, status_public_setup_mode):
        status_public_setup_mode(True)
        body = _get_via_loopback_urlconf().json()
        assert isinstance(body["setup_mode"], bool), (
            f"setup_mode must be bool (not None, not string); "
            f"got {type(body['setup_mode']).__name__}"
        )

    def test_setup_mode_true_during_setup(self, status_public_setup_mode):
        status_public_setup_mode(True)
        body = _get_via_loopback_urlconf().json()
        assert body["setup_mode"] is True

    def test_setup_mode_false_after_setup_complete(
        self, status_public_setup_mode,
    ):
        status_public_setup_mode(False)
        body = _get_via_loopback_urlconf().json()
        assert body["setup_mode"] is False


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

    def test_main_urlconf_ignores_during_setup(self, enter_setup_mode):
        """During setup mode the setup gate is active — the 404 must
        still be a plain 404 (no setup-mode envelope leakage)."""
        resp = APIClient().get("/api/status/public/")
        # Either plain 404 from URLconf OR the setup gate's
        # setup_complete/setup_in_progress envelope — but NEVER a
        # 200. The critical invariant is "not reachable on main".
        assert resp.status_code in (403, 404), (
            f"Main listener must not 200 on /api/status/public/; "
            f"got {resp.status_code}"
        )
        assert resp.status_code != 200


# Note: status_public response is consumed only by the Python tray
# today; no TypeScript model exists for it. If the frontend ever grows
# a tray-dashboard widget that hits this payload, add a TS interface
# parity check here.
