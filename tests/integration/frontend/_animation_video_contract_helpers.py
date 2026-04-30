# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the animation-video frontend-integration contract
tests.

Plain (non-fixture) helpers only — pytest fixtures live in
``conftest.py`` so they are auto-discovered.  Used by
``test_animation_video_contract.py`` and
``test_animation_video_immutable_contract.py``.
"""

from __future__ import annotations

from pathlib import Path

from workers.services.parts_check import registry

REPO_ROOT = Path(__file__).resolve().parents[3]
JOB_CREATE_FORM_TS = (
    REPO_ROOT / "manager" / "frontend" / "src" / "app"
    / "features" / "projects" / "job-create-form.component.ts"
)
JOB_CREATE_FORM_ERRORS_TS = (
    REPO_ROOT / "manager" / "frontend" / "src" / "app"
    / "features" / "projects" / "job-create-form.errors.ts"
)


def seed_status(status, error=None):
    """Publish a deterministic FFmpeg status into the registry.

    Mirrors ``test_ffmpeg_status_api.py``'s ``_seed_status`` helper but
    scoped to the two fields the animation-video tests touch.
    """
    registry._publish(
        "ffmpeg",
        registry.Status(status=status, error=error),
    )
