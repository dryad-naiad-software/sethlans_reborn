# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frontend-backend contract tests for ``GET /api/ffmpeg-status/`` —
``FFmpegStatusResponse`` (TS) ↔ DRF JSON response shape.

Verifies the JSON shape DRF actually emits matches the field set the
Angular ``FFmpegStatusService`` expects — i.e. catches schema drift
between ``manager/workers/serializers/ffmpeg_status.py`` and
``manager/frontend/src/app/core/services/ffmpeg-status.service.ts``.

Spec: ``development/specs/wizard-ffmpeg-rewrite.md`` FR §83-95 (status
API), FR §124-126 (frontend DTO is a single discriminated union),
AC §463-469.

Per-field type alignment, ``system-status.models.ts`` cross-validation,
and worker-token contract live in
``test_ffmpeg_status_contract_extras.py`` to keep this file under the
300-line Python limit.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from workers.services.parts_check import registry

from ._ffmpeg_contract_helpers import (
    EXPECTED_DETAILS_KEYS,
    EXPECTED_REGULAR_KEYS,
    FFMPEG_SERVICE_TS,
    URL,
    is_optional_field,
    parse_ts_interface,
    seed_ffmpeg_status,
)


@pytest.fixture(autouse=True)
def scoped_parts_state():
    """Snapshot/restore the FFmpeg registry status between tests.

    Mirrors the pattern from ``tests/integration/manager/test_ffmpeg_status_api.py``
    so seeded states do not leak across tests.
    """
    prior = registry.get_status("ffmpeg")
    yield
    registry._publish("ffmpeg", prior)


@pytest.fixture
def regular_user(db):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.create_user(
        username="contract_regular", password="pw", is_staff=False,
    )


@pytest.fixture
def regular_client(regular_user):
    client = APIClient()
    client.force_authenticate(user=regular_user)
    return client


@pytest.fixture
def ffmpeg_service_ts() -> str:
    return FFMPEG_SERVICE_TS.read_text(encoding="utf-8")


@pytest.mark.django_db
class TestFFmpegStatusResponseInterface:
    """``FFmpegStatusResponse`` (TS) ↔ ``/api/ffmpeg-status/`` JSON.

    The TS interface declares
    ``{ video_assembly_ready: boolean; ffmpeg?: FFmpegDetails }`` —
    a discriminated union per spec FR §124-126.  Verify the regular
    payload exposes ONLY ``video_assembly_ready`` and the admin
    payload exposes ``video_assembly_ready`` PLUS ``ffmpeg``.
    """

    def test_ts_interface_declares_expected_fields(
        self, ffmpeg_service_ts,
    ):
        fields = parse_ts_interface(
            ffmpeg_service_ts, "FFmpegStatusResponse",
        )
        assert set(fields.keys()) == EXPECTED_REGULAR_KEYS | {"ffmpeg"}, (
            f"FFmpegStatusResponse fields drifted: {fields}"
        )
        assert "boolean" in fields["video_assembly_ready"]
        # Spec FR §125 — `ffmpeg` is optional (`?:`).
        assert is_optional_field(
            ffmpeg_service_ts, "FFmpegStatusResponse", "ffmpeg",
        ), "FFmpegStatusResponse.ffmpeg must be optional (`ffmpeg?:`)"

    def test_regular_payload_matches_minimum_ts_shape(
        self, regular_client, ffmpeg_service_ts,
    ):
        seed_ffmpeg_status(
            status="ready", source="system", version="8.1",
            path="/usr/bin/ffmpeg",
        )
        resp = regular_client.get(URL)
        assert resp.status_code == 200
        # The TS DTO declares ``ffmpeg`` as optional, so a regular
        # payload that omits it is contract-conforming — but the
        # required ``video_assembly_ready`` key MUST be present and
        # MUST be a JSON boolean.
        assert "video_assembly_ready" in resp.data
        assert isinstance(resp.data["video_assembly_ready"], bool)
        # No unknown keys leak through (extra keys would surprise the
        # generated TS code-gen path).
        assert set(resp.data.keys()) == EXPECTED_REGULAR_KEYS

    def test_admin_payload_matches_full_ts_shape(
        self, admin_client, ffmpeg_service_ts,
    ):
        seed_ffmpeg_status(
            status="ready", source="system", version="8.1",
            path="/usr/bin/ffmpeg",
        )
        resp = admin_client.get(URL)
        assert resp.status_code == 200
        assert set(resp.data.keys()) == {"video_assembly_ready", "ffmpeg"}
        # Verify ``ffmpeg`` block carries every field declared in the
        # TS ``FFmpegDetails`` interface.
        details_fields = parse_ts_interface(
            ffmpeg_service_ts, "FFmpegDetails",
        )
        assert set(details_fields.keys()) == EXPECTED_DETAILS_KEYS, (
            f"FFmpegDetails fields drifted: {details_fields}"
        )
        assert set(resp.data["ffmpeg"].keys()) == EXPECTED_DETAILS_KEYS
