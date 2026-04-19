# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import pytest

pytestmark = pytest.skip(
    "Obsoleted by setup-auth-unification; replaced in follow-up test phase",
    allow_module_level=True,
)

"""
Integration tests for the setup wizard flow endpoints.

Exercises ``/api/setup/status/``, ``/api/setup/topology/``, and
``/api/setup/network/`` via the Django test client.  Verifies JSON
shapes, sentinel checkpoints, status code progression, and that
completed setup surfaces 404 on setup endpoints.
"""

import json

import pytest
from rest_framework.test import APIClient


@pytest.fixture()
def data_dir(tmp_path, settings):
    """Point ``settings.BASE_DIR`` to a temp directory.

    This isolates sentinel file I/O so tests don't pollute each other.
    """
    settings.BASE_DIR = tmp_path
    return tmp_path


# -------------------------------------------------------------------
# FR-A1: GET /api/setup/status/
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestSetupStatus:

    def test_status_no_sentinel(self, data_dir):
        """Status returns empty state when no sentinel exists."""
        client = APIClient()
        resp = client.get("/api/setup/status/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["complete"] is False
        assert body["topology"] is None
        assert body["current_step"] is None
        assert body["checkpoints"] == []

    def test_status_with_partial_sentinel(self, data_dir):
        """Status reflects checkpoints written so far."""
        from workers.services.sentinel import write_sentinel

        write_sentinel(data_dir, {
            "version": 1,
            "completed_at": None,
            "topology": "manager",
            "checkpoints": ["topology_chosen", "network_configured"],
        })
        client = APIClient()
        resp = client.get("/api/setup/status/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["topology"] == "manager"
        assert "topology_chosen" in body["checkpoints"]
        assert "network_configured" in body["checkpoints"]
        assert body["current_step"] == "database_configured"

    def test_status_returns_404_after_completion(self, data_dir):
        """Status returns 404 when sentinel has completed_at set."""
        from workers.services.sentinel import create_sentinel

        create_sentinel(data_dir, "manager", ["topology_chosen"])
        client = APIClient()
        resp = client.get("/api/setup/status/")
        assert resp.status_code == 404


# -------------------------------------------------------------------
# FR-A2: POST /api/setup/topology/
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestSetupTopology:

    def test_topology_valid_creates_sentinel(self, data_dir):
        """Valid topology creates sentinel with checkpoint."""
        client = APIClient()
        resp = client.post(
            "/api/setup/topology/",
            {"topology": "manager"},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        # Verify sentinel was written
        from workers.services.sentinel import read_sentinel
        sentinel = read_sentinel(data_dir)
        assert sentinel is not None
        assert sentinel["topology"] == "manager"
        assert "topology_chosen" in sentinel["checkpoints"]

    def test_topology_writes_topology_json(self, data_dir):
        """Topology creates topology.json for the launcher."""
        client = APIClient()
        client.post(
            "/api/setup/topology/",
            {"topology": "manager_worker"},
            format="json",
        )
        topology_path = data_dir / "topology.json"
        assert topology_path.exists()
        data = json.loads(topology_path.read_text())
        assert data["topology"] == "manager_worker"

    def test_topology_invalid_returns_400(self, data_dir):
        """Invalid topology value is rejected."""
        client = APIClient()
        resp = client.post(
            "/api/setup/topology/",
            {"topology": "invalid_choice"},
            format="json",
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_topology_missing_returns_400(self, data_dir):
        """Missing topology field is rejected."""
        client = APIClient()
        resp = client.post(
            "/api/setup/topology/", {}, format="json",
        )
        assert resp.status_code == 400

    @pytest.mark.parametrize("topo", [
        "manager", "manager_worker", "worker_only",
    ])
    def test_all_valid_topologies_accepted(self, data_dir, topo):
        """Each valid topology string is accepted."""
        client = APIClient()
        resp = client.post(
            "/api/setup/topology/",
            {"topology": topo},
            format="json",
        )
        assert resp.status_code == 200


# -------------------------------------------------------------------
# FR-A3: POST /api/setup/network/
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestSetupNetwork:

    def test_network_writes_ini_and_checkpoint(self, data_dir):
        """Network config writes manager.ini and appends checkpoint."""
        client = APIClient()
        resp = client.post(
            "/api/setup/network/",
            {"bind_host": "0.0.0.0", "bind_port": 0},
            format="json",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["bind_host"] == "0.0.0.0"

        # Check INI was written
        import configparser
        ini = configparser.ConfigParser()
        ini.read(data_dir / "manager.ini")
        assert ini.get("server", "host") == "0.0.0.0"

    def test_network_invalid_port_returns_400(self, data_dir):
        """Non-integer port is rejected."""
        client = APIClient()
        resp = client.post(
            "/api/setup/network/",
            {"bind_host": "0.0.0.0", "bind_port": "not_a_number"},
            format="json",
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_network_data_dir_override(self, data_dir):
        """Optional data_dir is written to manager.ini."""
        client = APIClient()
        resp = client.post(
            "/api/setup/network/",
            {
                "bind_host": "127.0.0.1",
                "bind_port": 0,
                "data_dir": "/custom/data",
            },
            format="json",
        )
        assert resp.status_code == 200

        import configparser
        ini = configparser.ConfigParser()
        ini.read(data_dir / "manager.ini")
        assert ini.get("server", "data_dir") == "/custom/data"


# -------------------------------------------------------------------
# Sentinel checkpoint accumulation
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestCheckpointAccumulation:

    def test_checkpoints_accumulate_across_steps(self, data_dir):
        """Multiple steps append distinct checkpoints."""
        from workers.services.sentinel import (
            append_checkpoint, read_sentinel, write_sentinel,
        )

        write_sentinel(data_dir, {
            "version": 1,
            "completed_at": None,
            "topology": "manager",
            "checkpoints": ["topology_chosen"],
        })

        append_checkpoint(data_dir, "network_configured")
        append_checkpoint(data_dir, "database_configured")
        append_checkpoint(data_dir, "admin_created")

        sentinel = read_sentinel(data_dir)
        assert sentinel["checkpoints"] == [
            "topology_chosen",
            "network_configured",
            "database_configured",
            "admin_created",
        ]

    def test_duplicate_checkpoint_is_idempotent(self, data_dir):
        """Appending the same checkpoint twice does not duplicate it."""
        from workers.services.sentinel import (
            append_checkpoint, read_sentinel, write_sentinel,
        )

        write_sentinel(data_dir, {
            "version": 1,
            "completed_at": None,
            "topology": "manager",
            "checkpoints": ["topology_chosen"],
        })

        append_checkpoint(data_dir, "topology_chosen")
        sentinel = read_sentinel(data_dir)
        assert sentinel["checkpoints"].count("topology_chosen") == 1


# -------------------------------------------------------------------
# Post-completion: setup endpoints return 404
# -------------------------------------------------------------------


@pytest.mark.django_db
class TestPostCompletionBlocking:

    def test_topology_returns_404_after_setup_complete(self, data_dir):
        """Topology endpoint returns 404 after setup fully completes."""
        from workers.services.sentinel import write_sentinel

        write_sentinel(data_dir, {
            "version": 1,
            "completed_at": "2026-04-13T12:00:00Z",
            "topology": "manager",
            "checkpoints": ["topology_chosen"],
        })
        client = APIClient()
        resp = client.post(
            "/api/setup/topology/",
            {"topology": "manager_worker"},
            format="json",
        )
        assert resp.status_code == 404

    def test_network_returns_404_after_completion(self, data_dir):
        """Network endpoint returns 404 after full completion."""
        from workers.services.sentinel import create_sentinel

        create_sentinel(data_dir, "manager", ["topology_chosen"])
        client = APIClient()
        resp = client.post(
            "/api/setup/network/",
            {"bind_host": "0.0.0.0", "bind_port": 0},
            format="json",
        )
        assert resp.status_code == 404
