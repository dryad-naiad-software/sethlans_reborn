# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``manager/workers/views/setup_status.py``.

Views now declare ``SessionAuthentication`` + ``IsSetupPhaseUser``.
Each test attaches a real session dict + pre-populated
``_setup_snapshot`` so the permission check passes; post-completion
behaviour is handled by the gate, not the view itself.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from rest_framework.test import APIRequestFactory

from workers.views.setup_status import (
    setup_network_view,
    setup_status_view,
    setup_topology_view,
)


@pytest.fixture
def api_rf():
    return APIRequestFactory()


def _setup_phase_request(rf_method, *args, **kwargs):
    """Invoke rf.<method> and decorate with setup-phase session state."""
    request = rf_method(*args, **kwargs)
    # DRF @api_view wraps the HttpRequest into a Request, but the
    # permission check accesses request.session directly — MagicMock-dict
    # stand-in works for read-only permission logic.
    session = {"setup_phase": True, "setup_session_id": "sid-1"}
    mock_session = MagicMock()
    mock_session.get = MagicMock(
        side_effect=lambda k, default=None: session.get(k, default),
    )
    request.session = mock_session
    # Pre-populate snapshot so IsSetupPhaseUser doesn't hit disk.
    request._setup_snapshot = {
        "complete": False, "phase": "topology", "session_id": None,
    }
    return request


@pytest.fixture(autouse=True)
def _patch_frozen(mocker):
    mocker.patch(
        "workers.views.setup_status.is_frozen", return_value=False,
    )


class TestSetupStatusView:

    def test_empty_when_no_sentinel(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_status.read_sentinel", return_value=None,
        )
        req = _setup_phase_request(api_rf.get, "/api/setup/status/")
        resp = setup_status_view(req)
        assert resp.status_code == 200
        assert resp.data == {
            "complete": False,
            "topology": None,
            "current_step": None,
            "checkpoints": [],
        }

    def test_in_progress_reports_next_step(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_status.read_sentinel",
            return_value={
                "version": 1, "completed_at": None,
                "topology": "manager_worker",
                "checkpoints": [
                    "topology_chosen", "network_configured",
                ],
            },
        )
        req = _setup_phase_request(api_rf.get, "/api/setup/status/")
        resp = setup_status_view(req)
        assert resp.status_code == 200
        assert resp.data["topology"] == "manager_worker"
        assert resp.data["current_step"] == "database_configured"


class TestSetupTopologyView:

    def test_accepts_valid_topology(self, api_rf, mocker, tmp_path):
        mocker.patch(
            "workers.views.setup_status.read_sentinel", return_value=None,
        )
        mocker.patch("workers.views.setup_status.write_sentinel")
        mocker.patch(
            "workers.views.setup_status._get_data_dir",
            return_value=tmp_path,
        )
        req = _setup_phase_request(
            api_rf.post, "/api/setup/topology/",
            {"topology": "manager"}, format="json",
        )
        resp = setup_topology_view(req)
        assert resp.status_code == 200
        assert resp.data == {"status": "ok"}

    @pytest.mark.parametrize("topology", [
        "manager", "manager_worker", "worker_only",
    ])
    def test_all_valid_topologies(
        self, api_rf, mocker, tmp_path, topology,
    ):
        mocker.patch(
            "workers.views.setup_status.read_sentinel", return_value=None,
        )
        mocker.patch("workers.views.setup_status.write_sentinel")
        mocker.patch(
            "workers.views.setup_status._get_data_dir",
            return_value=tmp_path,
        )
        req = _setup_phase_request(
            api_rf.post, "/api/setup/topology/",
            {"topology": topology}, format="json",
        )
        resp = setup_topology_view(req)
        assert resp.status_code == 200

    def test_rejects_invalid_topology(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_status.read_sentinel", return_value=None,
        )
        req = _setup_phase_request(
            api_rf.post, "/api/setup/topology/",
            {"topology": "nope"}, format="json",
        )
        resp = setup_topology_view(req)
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "invalid_input"


class TestSetupNetworkView:

    def test_accepts_valid_config(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_status.read_sentinel", return_value=None,
        )
        mocker.patch("workers.views.setup_status.write_manager_ini")
        mocker.patch("workers.views.setup_status.append_checkpoint")
        mocker.patch("socket.socket")
        req = _setup_phase_request(
            api_rf.post, "/api/setup/network/",
            {"bind_host": "0.0.0.0", "bind_port": 8080},
            format="json",
        )
        resp = setup_network_view(req)
        assert resp.status_code == 200
        assert resp.data["bind_port"] == 8080

    def test_rejects_non_integer_port(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_status.read_sentinel", return_value=None,
        )
        req = _setup_phase_request(
            api_rf.post, "/api/setup/network/",
            {"bind_host": "0.0.0.0", "bind_port": "not_a_number"},
            format="json",
        )
        resp = setup_network_view(req)
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "invalid_input"

    def test_rejects_unavailable_port(self, api_rf, mocker):
        mocker.patch(
            "workers.views.setup_status.read_sentinel", return_value=None,
        )
        mock_sock = MagicMock()
        mock_sock.__enter__ = MagicMock(return_value=mock_sock)
        mock_sock.__exit__ = MagicMock(return_value=False)
        mock_sock.bind.side_effect = OSError("Address in use")
        mocker.patch("socket.socket", return_value=mock_sock)
        req = _setup_phase_request(
            api_rf.post, "/api/setup/network/",
            {"bind_host": "0.0.0.0", "bind_port": 8080},
            format="json",
        )
        resp = setup_network_view(req)
        assert resp.status_code == 400
        assert resp.data["error"]["code"] == "invalid_input"
