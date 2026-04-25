# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Regression tests for issue #127: ``_infer_current_step`` must not
return ``ffmpeg_installed`` when the chosen topology is
``worker_only``.

Before the fix, a worker_only operator who finished
``admin_created`` would be sent back to the FFmpeg step — a step
that has no UI for that topology and that the manager never needs
because worker_only managers do not render.  Post-fix, the step
inference jumps straight to the post-admin step that *does* exist
for worker_only (``verified``).
"""

from __future__ import annotations

import pytest

from workers.views.setup_status import _infer_current_step


# Checkpoints up through the admin step — common starting point for
# the post-admin-step assertion.  worker_only and manager skip the
# ``worker_password_set`` step, so this is the latest checkpoint
# they will have prior to the next branch.
_AFTER_ADMIN = [
    "topology_chosen",
    "network_configured",
    "database_configured",
    "admin_created",
]

# manager_worker also requires worker_password_set before the
# ffmpeg step.  Use this list to assert manager_worker behaviour.
_AFTER_WORKER_PASSWORD = _AFTER_ADMIN + ["worker_password_set"]


class TestInferCurrentStepTopologyGate:
    """Topology gating in ``_infer_current_step``."""

    def test_worker_only_skips_ffmpeg_after_admin(self):
        """worker_only must jump to ``verified`` after admin (#127).

        Before the fix this returned ``ffmpeg_installed`` and the
        wizard would dead-end on a step that has no UI for
        worker_only.
        """
        step = _infer_current_step(_AFTER_ADMIN, "worker_only")

        assert step != "ffmpeg_installed", (
            "worker_only must not return ffmpeg_installed (#127)."
        )
        assert step == "verified", (
            f"Expected 'verified' as the post-admin step for "
            f"worker_only; got {step!r}"
        )

    def test_manager_returns_ffmpeg_installed_after_admin(self):
        """manager (no embedded worker) still needs ffmpeg."""
        step = _infer_current_step(_AFTER_ADMIN, "manager")

        assert step == "ffmpeg_installed"

    def test_manager_worker_returns_ffmpeg_after_worker_password(self):
        """manager_worker still flows through ffmpeg after worker
        password is set — gate fix must not have disturbed this."""
        step = _infer_current_step(
            _AFTER_WORKER_PASSWORD, "manager_worker",
        )

        assert step == "ffmpeg_installed"

    @pytest.mark.parametrize("checkpoints,expected", [
        # worker_only completes verify directly after admin.
        (_AFTER_ADMIN + ["verified"], None),
    ])
    def test_worker_only_completes_without_ffmpeg_or_blender(
        self, checkpoints, expected,
    ):
        """worker_only setup is complete once verified — no
        ffmpeg_installed or blender_predownloaded should ever be
        required."""
        assert _infer_current_step(checkpoints, "worker_only") == expected
