# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Frontend-backend contract tests for animation update-time
``video_settings_immutable`` rejection shape.

Spec FR §135-138 / AC §480 — PATCHing ``video_settings`` to a
different value (or to ``None``) on an existing animation returns 400
with the closed-vocab code ``video_settings_immutable`` under the
``video_settings`` field key.  Same dict-keyed shape as the create-time
rejection in ``test_animation_video_contract.py``, so the frontend
needs only ONE error-parsing branch for both paths.
"""

from __future__ import annotations

import pytest

from ._animation_video_contract_helpers import seed_status

pytestmark = pytest.mark.usefixtures("scoped_parts_state")


@pytest.mark.django_db
class TestVideoSettingsImmutableErrorShape:
    """Spec AC §480 — PATCH rejection emits the immutability code.

    The serializer raises with a dict-keyed payload
    (``{"video_settings": ["video_settings_immutable"]}``), so the
    frontend can use the same ``err.error?.['video_settings']?.[0]``
    access pattern as the create-time rejection.
    """

    def _create_with_video(
        self, admin_client, animation_payload, video_settings,
    ):
        seed_status("ready")
        animation_payload["video_settings"] = video_settings
        resp = admin_client.post(
            "/api/animations/", animation_payload, format="json",
        )
        assert resp.status_code == 201, resp.data
        return resp.data["id"]

    def test_patch_to_different_settings_returns_immutable_code(
        self, admin_client, animation_payload, video_settings,
    ):
        anim_id = self._create_with_video(
            admin_client, animation_payload, video_settings,
        )
        resp = admin_client.patch(
            f"/api/animations/{anim_id}/",
            {"video_settings": {"preset": "hq_h265", "framerate": 30}},
            format="json",
        )
        assert resp.status_code == 400
        assert "video_settings" in resp.data
        # Standard DRF dict-keyed shape.  The frontend uses the same
        # extraction pattern as the create-time rejection.
        assert "video_settings_immutable" in str(
            resp.data["video_settings"],
        )

    def test_patch_to_null_is_also_rejected(
        self, admin_client, animation_payload, video_settings,
    ):
        """Spec FR §137 says ANY change is rejected — adding,
        removing, or replacing.  Setting to None must trip the same
        immutability guard."""
        anim_id = self._create_with_video(
            admin_client, animation_payload, video_settings,
        )
        resp = admin_client.patch(
            f"/api/animations/{anim_id}/",
            {"video_settings": None},
            format="json",
        )
        assert resp.status_code == 400
        assert "video_settings_immutable" in str(resp.data)

    def test_immutability_error_is_under_video_settings_key(
        self, admin_client, animation_payload, video_settings,
    ):
        """Frontend access pattern: the immutability error surfaces at
        ``err.error?.['video_settings']?.[0]`` — the SAME key as the
        create-time ``video_assembly_unavailable`` rejection.  This
        means the form's error parser only needs ONE branch to handle
        both rejection paths."""
        anim_id = self._create_with_video(
            admin_client, animation_payload, video_settings,
        )
        resp = admin_client.patch(
            f"/api/animations/{anim_id}/",
            {"video_settings": {"preset": "hq_h265", "framerate": 30}},
            format="json",
        )
        assert resp.status_code == 400
        # Mirror the TS extraction pattern.
        body = resp.data
        first_code = (
            body.get("video_settings", [None])[0]
            if isinstance(body.get("video_settings"), list)
            else None
        )
        assert first_code == "video_settings_immutable", (
            f"Frontend access pattern returned {first_code!r}; "
            f"full body: {body!r}"
        )
