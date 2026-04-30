# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Regression tests for ``_infer_current_step`` topology gating.

Originally for issue #127 (worker_only must not be sent back to a
non-existent FFmpeg step).  After spec ``wizard-ffmpeg-rewrite``, the
wizard's FFmpeg step has been deleted entirely; FFmpeg acquisition is
a runtime concern owned by the manager-side parts-check.  The
remaining stepper logic now jumps from ``admin_created`` straight to
``verified`` for ``manager`` and ``worker_only`` topologies, and from
``worker_password_set`` to ``blender_predownloaded`` for
``manager_worker``.
"""

from __future__ import annotations

import pytest

from workers.views.setup_status import _infer_current_step


# Checkpoints up through the admin step — common starting point for
# the post-admin-step assertion.  ``worker_only`` and ``manager`` skip
# ``worker_password_set``; ``manager_worker`` requires it before the
# next branch.
_AFTER_ADMIN = [
    "topology_chosen",
    "network_configured",
    "database_configured",
    "admin_created",
]

# manager_worker also requires worker_password_set before blender.
_AFTER_WORKER_PASSWORD = _AFTER_ADMIN + ["worker_password_set"]


class TestInferCurrentStepTopologyGate:
    """Topology gating in ``_infer_current_step``."""

    def test_worker_only_jumps_to_verified_after_admin(self):
        """``worker_only`` jumps straight to ``verified`` after admin."""
        step = _infer_current_step(_AFTER_ADMIN, "worker_only")
        assert step == "verified", (
            f"Expected 'verified' as the post-admin step for "
            f"worker_only; got {step!r}"
        )

    def test_manager_jumps_to_verified_after_admin(self):
        """``manager`` (no embedded worker) goes to ``verified`` after
        admin now that the FFmpeg wizard step is gone."""
        step = _infer_current_step(_AFTER_ADMIN, "manager")
        assert step == "verified"

    def test_manager_worker_returns_blender_after_worker_password(self):
        """``manager_worker`` flows to ``blender_predownloaded`` after
        the worker password is set."""
        step = _infer_current_step(
            _AFTER_WORKER_PASSWORD, "manager_worker",
        )
        assert step == "blender_predownloaded"

    @pytest.mark.parametrize("checkpoints,expected", [
        # worker_only completes verify directly after admin.
        (_AFTER_ADMIN + ["verified"], None),
    ])
    def test_worker_only_completes_without_blender(
        self, checkpoints, expected,
    ):
        """``worker_only`` setup is complete once verified — no
        ``blender_predownloaded`` should ever be required."""
        assert _infer_current_step(checkpoints, "worker_only") == expected
