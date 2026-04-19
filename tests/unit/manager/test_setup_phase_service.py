# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``manager/workers/services/setup_phase.py``.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from workers.services import setup_phase as sp
from workers.services.setup_phase import (
    SETUP_PHASES,
    SetupPhaseError,
    read_setup_progress,
    require_setup_phase,
    setup_state_snapshot,
)


def _write_sentinel(data_dir, checkpoints=None, completed_at=None,
                    topology="manager"):
    payload = {
        "version": 1,
        "completed_at": completed_at,
        "topology": topology,
        "checkpoints": list(checkpoints or []),
    }
    (data_dir / ".setup_complete").write_text(json.dumps(payload))


# ---- read_setup_progress ------------------------------------------------

class TestReadSetupProgress:

    def test_no_sentinel_returns_none(self, tmp_path):
        assert read_setup_progress(tmp_path) is None

    def test_completed_sentinel_returns_none(self, tmp_path):
        _write_sentinel(tmp_path, completed_at="2025-01-15T00:00:00Z")
        assert read_setup_progress(tmp_path) is None

    def test_empty_checkpoints_returns_first_phase(self, tmp_path):
        _write_sentinel(tmp_path, checkpoints=[])
        assert read_setup_progress(tmp_path) == "topology"

    def test_partial_progress_returns_next_phase(self, tmp_path):
        _write_sentinel(
            tmp_path,
            checkpoints=[
                "topology_chosen", "network_configured",
                "database_configured",
            ],
        )
        assert read_setup_progress(tmp_path) == "admin"

    def test_all_checkpoints_returns_none(self, tmp_path):
        all_checkpoints = [
            "topology_chosen", "network_configured", "database_configured",
            "admin_created", "worker_password_set", "ffmpeg_installed",
            "blender_predownloaded", "verified",
        ]
        _write_sentinel(tmp_path, checkpoints=all_checkpoints)
        assert read_setup_progress(tmp_path) is None


# ---- require_setup_phase ------------------------------------------------

class TestRequireSetupPhase:

    def test_happy_path(self, tmp_path):
        _write_sentinel(tmp_path, checkpoints=[])
        require_setup_phase("topology", tmp_path)  # no exception

    def test_mismatch_raises(self, tmp_path):
        _write_sentinel(tmp_path, checkpoints=[])
        with pytest.raises(SetupPhaseError) as exc_info:
            require_setup_phase("admin", tmp_path)
        err = exc_info.value
        assert err.code == "precondition_unmet"
        assert err.status == 409
        assert err.details == {"expected": "admin", "current": "topology"}

    def test_unknown_phase_raises_value_error(self, tmp_path):
        _write_sentinel(tmp_path, checkpoints=[])
        with pytest.raises(ValueError):
            require_setup_phase("not_a_phase", tmp_path)

    def test_phases_enum_is_canonical(self):
        assert "topology" in SETUP_PHASES
        assert "verify" in SETUP_PHASES


# ---- setup_state_snapshot ------------------------------------------------

class TestSetupStateSnapshot:

    def test_snapshot_caches_on_request(self, tmp_path, mocker):
        mocker.patch.object(
            sp, "read_sentinel", return_value=None,
        )
        mocker.patch(
            "sethlans_manager.middleware.setup_gate._get_data_dir",
            return_value=tmp_path,
        )
        mocker.patch.object(
            sp, "read_setup_progress", return_value="topology",
        )
        mocker.patch(
            "workers.services.ini_atomic.read_setup_session_id",
            return_value=None,
        )
        request = MagicMock(spec=["_setup_snapshot"])
        # Clear attr so getattr returns None initially
        if hasattr(request, "_setup_snapshot"):
            delattr(request, "_setup_snapshot")
        snap1 = setup_state_snapshot(request)
        snap2 = setup_state_snapshot(request)
        assert snap1 is snap2
        assert snap1["phase"] == "topology"
        assert snap1["complete"] is False

    def test_snapshot_reports_complete(self, tmp_path, mocker):
        mocker.patch.object(
            sp, "read_sentinel",
            return_value={"completed_at": "2025-01-15T00:00:00Z"},
        )
        mocker.patch(
            "sethlans_manager.middleware.setup_gate._get_data_dir",
            return_value=tmp_path,
        )
        mocker.patch(
            "workers.services.ini_atomic.read_setup_session_id",
            return_value="sid-1",
        )
        request = MagicMock(spec=["_setup_snapshot"])
        if hasattr(request, "_setup_snapshot"):
            delattr(request, "_setup_snapshot")
        snap = setup_state_snapshot(request)
        assert snap["complete"] is True
        assert snap["phase"] is None
        assert snap["session_id"] == "sid-1"
