# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``manager/workers/views/setup_restart.py``.
"""

from __future__ import annotations

import os
import platform
import stat
from unittest.mock import MagicMock

import pytest
from rest_framework.test import APIRequestFactory

from workers.views import setup_restart as restart_mod
from workers.views.setup_restart import setup_restart_view


SENTINEL_COMPLETE = {
    "version": 1,
    "completed_at": "2025-01-15T12:00:00Z",
    "topology": "manager",
    "checkpoints": [],
}


def _make_request(setup_phase=True):
    rf = APIRequestFactory()
    request = rf.post("/api/setup/restart/", data={}, format="json")
    request.session = MagicMock()
    request.session.get = MagicMock(side_effect=lambda k, default=None: {
        "setup_phase": True if setup_phase else None,
        "setup_session_id": "abc" if setup_phase else None,
    }.get(k, default))
    request._setup_snapshot = {
        "complete": False, "phase": "verify", "session_id": None,
    }
    request.user = MagicMock(is_authenticated=False)
    return request


class TestRestartSuccess:

    def test_creates_marker_returns_202(self, mocker, tmp_path):
        mocker.patch.object(
            restart_mod, "_data_dir", return_value=tmp_path,
        )
        mocker.patch.object(
            restart_mod, "read_sentinel", return_value=SENTINEL_COMPLETE,
        )
        response = setup_restart_view(_make_request())
        assert response.status_code == 202
        marker = tmp_path / restart_mod.RESTART_MARKER_NAME
        assert marker.exists()

    @pytest.mark.skipif(
        platform.system() == "Windows",
        reason="POSIX mode bits only",
    )
    def test_marker_has_mode_0o600(self, mocker, tmp_path):
        mocker.patch.object(
            restart_mod, "_data_dir", return_value=tmp_path,
        )
        mocker.patch.object(
            restart_mod, "read_sentinel", return_value=SENTINEL_COMPLETE,
        )
        setup_restart_view(_make_request())
        marker = tmp_path / restart_mod.RESTART_MARKER_NAME
        mode = stat.S_IMODE(os.stat(marker).st_mode)
        assert mode == 0o600


class TestRestartPreconditions:

    def test_no_sentinel_returns_409(self, mocker, tmp_path):
        mocker.patch.object(
            restart_mod, "_data_dir", return_value=tmp_path,
        )
        mocker.patch.object(
            restart_mod, "read_sentinel", return_value=None,
        )
        response = setup_restart_view(_make_request())
        assert response.status_code == 409
        assert response.data["error"]["code"] == "precondition_unmet"

    def test_sentinel_without_completed_at_returns_409(
        self, mocker, tmp_path,
    ):
        mocker.patch.object(
            restart_mod, "_data_dir", return_value=tmp_path,
        )
        mocker.patch.object(
            restart_mod, "read_sentinel",
            return_value={"version": 1, "completed_at": None,
                          "topology": "manager", "checkpoints": []},
        )
        response = setup_restart_view(_make_request())
        assert response.status_code == 409

    def test_second_request_returns_409(self, mocker, tmp_path):
        mocker.patch.object(
            restart_mod, "_data_dir", return_value=tmp_path,
        )
        mocker.patch.object(
            restart_mod, "read_sentinel", return_value=SENTINEL_COMPLETE,
        )
        assert setup_restart_view(_make_request()).status_code == 202
        second = setup_restart_view(_make_request())
        assert second.status_code == 409
        assert second.data["error"]["code"] == "precondition_unmet"


class TestRestartContainment:

    def test_containment_check_helper(self, tmp_path):
        good = tmp_path / ".restart_requested"
        bad = tmp_path.parent / ".restart_requested"
        assert restart_mod._containment_ok(tmp_path, good) is True
        assert restart_mod._containment_ok(tmp_path, bad) is False

    def test_containment_failure_returns_500(self, mocker, tmp_path):
        mocker.patch.object(
            restart_mod, "_data_dir", return_value=tmp_path,
        )
        mocker.patch.object(
            restart_mod, "read_sentinel", return_value=SENTINEL_COMPLETE,
        )
        mocker.patch.object(
            restart_mod, "_containment_ok", return_value=False,
        )
        response = setup_restart_view(_make_request())
        assert response.status_code == 500
        assert response.data["error"]["code"] == "internal_error"
