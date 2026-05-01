# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
FR-DEL10 — post-deletion API surface verification.

After Spec 2 cluster B1 (FR-DEL2 / FR-DEL3 / FR-DEL4) the manager
process MUST return HTTP 404 for every URL under ``/api/setup/*`` from
a fully-set-up manager.  Each pre-existing wizard endpoint is asserted
explicitly so a regression that re-introduces any ``/api/setup/*``
route surfaces here.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


# Every URL that existed under the deleted ``workers.urls_setup`` plus
# the FFmpeg endpoints required by FR-DEL10.  The path content for the
# ``<task>`` placeholders is arbitrary — the resolver no longer matches
# either the prefix or the parameterised form.
DELETED_SETUP_ENDPOINTS = [
    "/api/setup/status/",
    "/api/setup/topology/",
    "/api/setup/network/",
    "/api/setup/database/",
    "/api/setup/admin-user/",
    "/api/setup/worker-password/",
    "/api/setup/ffmpeg/start/",
    "/api/setup/ffmpeg/progress/abc123/",
    "/api/setup/ffmpeg/cancel/",
    "/api/setup/blender/start/",
    "/api/setup/blender/progress/abc123/",
    "/api/setup/blender/cancel/",
    "/api/setup/verify/",
    "/api/setup/summary/",
]


@pytest.mark.parametrize("path", DELETED_SETUP_ENDPOINTS)
def test_setup_endpoint_returns_404_get(path):
    """GET on every deleted setup endpoint MUST 404."""
    resp = APIClient().get(path)
    assert resp.status_code == 404, (
        f"GET {path} expected 404, got {resp.status_code}"
    )


@pytest.mark.parametrize("path", DELETED_SETUP_ENDPOINTS)
def test_setup_endpoint_returns_404_post(path):
    """POST on every deleted setup endpoint MUST 404.

    POST exercises a different code path than GET (CSRF, body parsing
    in some prior implementations) — assert the resolver still rejects
    the URL regardless of method.
    """
    resp = APIClient().post(path, data={}, format="json")
    assert resp.status_code == 404, (
        f"POST {path} expected 404, got {resp.status_code}"
    )


def test_setup_endpoints_404_for_admin_session(admin_client):
    """An admin session MUST also see 404 — the routes are gone, not gated."""
    for path in DELETED_SETUP_ENDPOINTS:
        resp = admin_client.get(path)
        assert resp.status_code == 404, (
            f"admin GET {path} expected 404, got {resp.status_code}"
        )
