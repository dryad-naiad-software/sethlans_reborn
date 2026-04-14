# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``manager/workers/views/setup_status.py``."""

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


@pytest.fixture(autouse=True)
def _patch_frozen(mocker):
    mocker.patch(
        'workers.views.setup_status.is_frozen', return_value=False,
    )


# ---- GET /api/setup/status/ ------------------------------------------------

class TestSetupStatusView:

    def test_returns_empty_state_when_no_sentinel(
        self, api_rf, mocker,
    ):
        mocker.patch(
            'workers.views.setup_status.read_sentinel',
            return_value=None,
        )
        request = api_rf.get('/api/setup/status/')
        response = setup_status_view(request)
        assert response.status_code == 200
        assert response.data == {
            "complete": False,
            "topology": None,
            "current_step": None,
            "checkpoints": [],
        }

    def test_returns_404_when_setup_complete(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_status.read_sentinel',
            return_value={
                "version": 1,
                "completed_at": "2025-01-15T12:00:00Z",
                "topology": "manager",
                "checkpoints": ["topology_chosen"],
            },
        )
        request = api_rf.get('/api/setup/status/')
        response = setup_status_view(request)
        assert response.status_code == 404

    def test_returns_in_progress_state(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_status.read_sentinel',
            return_value={
                "version": 1,
                "completed_at": None,
                "topology": "manager_worker",
                "checkpoints": [
                    "topology_chosen", "network_configured",
                ],
            },
        )
        request = api_rf.get('/api/setup/status/')
        response = setup_status_view(request)
        assert response.status_code == 200
        assert response.data["complete"] is False
        assert response.data["topology"] == "manager_worker"
        assert response.data["current_step"] == "database_configured"
        assert "topology_chosen" in response.data["checkpoints"]


# ---- POST /api/setup/topology/ ---------------------------------------------

class TestSetupTopologyView:

    def test_accepts_valid_topology(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_status.read_sentinel',
            return_value=None,
        )
        mock_write = mocker.patch(
            'workers.views.setup_status.write_sentinel',
        )
        request = api_rf.post(
            '/api/setup/topology/',
            {"topology": "manager"}, format='json',
        )
        response = setup_topology_view(request)
        assert response.status_code == 200
        assert response.data == {"status": "ok"}
        mock_write.assert_called_once()

    @pytest.mark.parametrize("topology", [
        "manager", "manager_worker", "worker_only",
    ])
    def test_all_valid_topologies_accepted(
        self, api_rf, mocker, topology,
    ):
        mocker.patch(
            'workers.views.setup_status.read_sentinel',
            return_value=None,
        )
        mocker.patch('workers.views.setup_status.write_sentinel')
        request = api_rf.post(
            '/api/setup/topology/',
            {"topology": topology}, format='json',
        )
        response = setup_topology_view(request)
        assert response.status_code == 200

    def test_rejects_invalid_topology(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_status.read_sentinel',
            return_value=None,
        )
        request = api_rf.post(
            '/api/setup/topology/',
            {"topology": "invalid_value"}, format='json',
        )
        response = setup_topology_view(request)
        assert response.status_code == 400
        assert "error" in response.data

    def test_returns_404_when_setup_complete(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_status.read_sentinel',
            return_value={
                "version": 1,
                "completed_at": "2025-01-15T12:00:00Z",
                "topology": "manager",
                "checkpoints": [],
            },
        )
        request = api_rf.post(
            '/api/setup/topology/',
            {"topology": "manager"}, format='json',
        )
        response = setup_topology_view(request)
        assert response.status_code == 404

    def test_idempotent_overwrites_previous(self, api_rf, mocker):
        """Last write wins: re-posting resets checkpoints."""
        existing = {
            "version": 1,
            "completed_at": None,
            "topology": "manager",
            "checkpoints": [
                "topology_chosen", "network_configured",
            ],
        }
        mocker.patch(
            'workers.views.setup_status.read_sentinel',
            side_effect=[None, existing],
        )
        mock_write = mocker.patch(
            'workers.views.setup_status.write_sentinel',
        )
        request = api_rf.post(
            '/api/setup/topology/',
            {"topology": "manager_worker"}, format='json',
        )
        response = setup_topology_view(request)
        assert response.status_code == 200
        written = mock_write.call_args[0][1]
        assert written["topology"] == "manager_worker"
        assert written["checkpoints"] == ["topology_chosen"]


# ---- POST /api/setup/network/ ----------------------------------------------

class TestSetupNetworkView:

    def test_accepts_valid_config(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_status.read_sentinel',
            return_value=None,
        )
        mocker.patch(
            'workers.views.setup_status.write_manager_ini',
        )
        mocker.patch(
            'workers.views.setup_status.append_checkpoint',
        )
        mocker.patch('socket.socket')
        request = api_rf.post(
            '/api/setup/network/',
            {"bind_host": "0.0.0.0", "bind_port": 8080},
            format='json',
        )
        response = setup_network_view(request)
        assert response.status_code == 200
        assert response.data["bind_port"] == 8080

    def test_rejects_non_integer_port(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_status.read_sentinel',
            return_value=None,
        )
        request = api_rf.post(
            '/api/setup/network/',
            {"bind_host": "0.0.0.0", "bind_port": "not_a_number"},
            format='json',
        )
        response = setup_network_view(request)
        assert response.status_code == 400
        assert "integer" in response.data["error"].lower()

    def test_rejects_unavailable_port(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_status.read_sentinel',
            return_value=None,
        )
        mock_sock = MagicMock()
        mock_sock.__enter__ = MagicMock(return_value=mock_sock)
        mock_sock.__exit__ = MagicMock(return_value=False)
        mock_sock.bind.side_effect = OSError("Address in use")
        mocker.patch('socket.socket', return_value=mock_sock)
        request = api_rf.post(
            '/api/setup/network/',
            {"bind_host": "0.0.0.0", "bind_port": 8080},
            format='json',
        )
        response = setup_network_view(request)
        assert response.status_code == 400
        assert "not available" in response.data["error"].lower()

    def test_writes_ini_and_checkpoint(self, api_rf, mocker):
        mocker.patch(
            'workers.views.setup_status.read_sentinel',
            return_value=None,
        )
        mock_ini = mocker.patch(
            'workers.views.setup_status.write_manager_ini',
        )
        mock_cp = mocker.patch(
            'workers.views.setup_status.append_checkpoint',
        )
        mocker.patch('socket.socket')
        request = api_rf.post(
            '/api/setup/network/',
            {"bind_host": "127.0.0.1", "bind_port": 9090},
            format='json',
        )
        response = setup_network_view(request)
        assert response.status_code == 200
        call_args = mock_ini.call_args[0][0]
        assert call_args["server.host"] == "127.0.0.1"
        assert call_args["server.port"] == "9090"
        mock_cp.assert_called_once()
