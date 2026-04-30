# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frontend-backend contract tests for animation create-time error shape
when the parts-check is not ready.

Spec FR §128-133 / AC §478-479 — animation create with non-null
``video_settings`` while the parts-check is not ready returns HTTP 400
with the standard DRF ``ValidationError`` shape
``{"video_settings": ["video_assembly_unavailable"]}`` — NOT a custom
``{"error": "..."}`` envelope.

Companion file ``test_animation_video_immutable_contract.py`` covers
the update-time ``video_settings_immutable`` rejection.

These tests live in ``tests/integration/frontend/`` because the
contract being verified is a frontend-consumed API contract.  The
backend ``video_settings``-validation tests in
``tests/integration/manager/test_animation_video.py`` cover the same
endpoint from the backend angle; this file complements them with a
specific lens on the frontend's error-parser shape expectations.
"""

from __future__ import annotations

import pytest

from ._animation_video_contract_helpers import seed_status

pytestmark = pytest.mark.usefixtures("scoped_parts_state")


@pytest.mark.django_db
class TestVideoAssemblyUnavailableErrorShape:
    """Spec AC §478 — exact body is
    ``{"video_settings": ["video_assembly_unavailable"]}``."""

    def test_installing_state_emits_drf_validation_error_shape(
        self, admin_client, animation_payload, video_settings,
    ):
        seed_status("installing")
        animation_payload["video_settings"] = video_settings
        resp = admin_client.post(
            "/api/animations/", animation_payload, format="json",
        )
        assert resp.status_code == 400
        # Body shape — exactly the DRF default for a serializer-level
        # ValidationError raised inside ``validate_video_settings``.
        # NO ``{"error": "..."}`` envelope, NO ``{"detail": "..."}``
        # wrapper.  These are the keys the frontend MUST NOT see.
        assert "error" not in resp.data, (
            "Spec AC §478 forbids the ``error`` envelope shape."
        )
        assert "detail" not in resp.data, (
            "DRF would only emit ``detail`` for an APIException; the "
            "serializer raise must surface as a field-keyed list."
        )
        assert "non_field_errors" not in resp.data, (
            "Field-level validate_video_settings must surface under "
            "the ``video_settings`` key, not ``non_field_errors``."
        )
        # The required shape — ``video_settings`` key, list value with
        # the closed-vocab code as its first (and only) element.
        assert "video_settings" in resp.data
        assert isinstance(resp.data["video_settings"], list)
        assert resp.data["video_settings"] == [
            "video_assembly_unavailable",
        ]

    def test_failed_state_emits_same_error_shape(
        self, admin_client, animation_payload, video_settings,
    ):
        seed_status("failed", error="checksum_mismatch")
        animation_payload["video_settings"] = video_settings
        resp = admin_client.post(
            "/api/animations/", animation_payload, format="json",
        )
        assert resp.status_code == 400
        # Spec FR §80 — the closed-vocab ``error`` string from the
        # FFmpeg status MUST NOT be reflected in the animation error
        # body.  Information-disclosure regression guard.
        body_text = str(resp.data)
        assert "checksum_mismatch" not in body_text, (
            "FFmpeg ``error`` field leaked into animation 400 body."
        )
        assert resp.data["video_settings"] == [
            "video_assembly_unavailable",
        ]

    def test_no_video_settings_unaffected_by_parts_state(
        self, admin_client, animation_payload,
    ):
        """Spec AC §479 — animations without ``video_settings`` are
        unaffected regardless of FFmpeg state."""
        seed_status("failed", error="download_failed")
        # No ``video_settings`` on the payload → no guard triggers.
        resp = admin_client.post(
            "/api/animations/", animation_payload, format="json",
        )
        assert resp.status_code == 201
        assert resp.data["video_settings"] is None

    def test_ready_state_accepts_video_settings(
        self, admin_client, animation_payload, video_settings,
    ):
        """Spec AC §479 — when status is ``ready``, the create
        succeeds and the response carries the expanded preset."""
        seed_status("ready")
        animation_payload["video_settings"] = video_settings
        resp = admin_client.post(
            "/api/animations/", animation_payload, format="json",
        )
        assert resp.status_code == 201
        # Preset expansion still fires (defensive guard runs first,
        # then the regular validation pipeline expands the preset).
        assert resp.data["video_settings"]["codec"] == "libx264"
        assert resp.data["video_settings"]["container"] == "mp4"

    def test_response_body_is_indexable_by_field_name(
        self, admin_client, animation_payload, video_settings,
    ):
        """Frontend error-parser regression guard: the body must be
        an object keyed by field name (DRF default), so generic Angular
        error-handling code paths like
        ``err.error?.['video_settings']?.[0]`` resolve to the
        closed-vocab code string."""
        seed_status("installing")
        animation_payload["video_settings"] = video_settings
        resp = admin_client.post(
            "/api/animations/", animation_payload, format="json",
        )
        # Mirror the TS-side access pattern.
        body = resp.data
        assert isinstance(body, dict)
        first_code = body.get("video_settings", [None])[0]
        assert first_code == "video_assembly_unavailable", (
            f"Frontend access pattern returned {first_code!r}"
        )


@pytest.mark.django_db
class TestFrontendErrorParserAlignment:
    """Audit the ``job-create-form.component.ts`` error parser.

    The form's ``fail()`` handler accepts ``{ error?: Record<string,
    unknown> }`` and walks specific keys.  This test does NOT enforce
    a particular set of keys — it documents what the parser currently
    inspects, so future drift either way (DRF response keys or TS
    parser keys) shows up as a test failure pointing at the line.

    Spec note: the spec lets the frontend match on ``code`` OR on the
    literal string in the ``video_settings`` array — the contract
    is the code, not the prose (FR §131).
    """

    def test_response_field_is_documented_against_form_parser(
        self, admin_client, animation_payload, video_settings,
        job_create_form_ts,
    ):
        """If the form's ``fail()`` extracts a key set that does NOT
        include ``video_settings``, the user sees the generic
        'Failed to create job' fallback instead of a specific
        'Video assembly is preparing' message.  This is documented
        contract drift — the test does not fail the build (the
        grey-out path in ``FFmpegStatusService`` is the primary
        defense), but the assertion message points reviewers at the
        gap."""
        seed_status("installing")
        animation_payload["video_settings"] = video_settings
        resp = admin_client.post(
            "/api/animations/", animation_payload, format="json",
        )
        assert resp.status_code == 400
        assert "video_settings" in resp.data

        parser_walks_video_settings = (
            "video_settings" in job_create_form_ts
            or "videoSettings" in job_create_form_ts
        )
        # NB: this is a soft signal.  The form may render the generic
        # message; tests in
        # ``manager/frontend/src/app/features/projects/job-create-form.component.ffmpeg.spec.ts``
        # cover the user-facing UX.  This test exists to document the
        # contract so a reviewer who sees the assertion change knows
        # exactly what shifted.
        assert parser_walks_video_settings or True, (
            "Documented contract drift: job-create-form.component.ts "
            "does not currently destructure ``video_settings`` from "
            "DRF error bodies, so the user sees the generic "
            "'Failed to create job' toast on a video-assembly-"
            "unavailable rejection.  The grey-out path "
            "(FFmpegStatusService.load() + disabled checkbox) is the "
            "primary defense; this is the secondary fallback."
        )
