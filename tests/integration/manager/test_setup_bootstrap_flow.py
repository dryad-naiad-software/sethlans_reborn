# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Bootstrap happy-path integration (spec setup-auth-unification).

Exercises the full pipeline: bootstrap → session cookie → authenticated
setup POST → rejected call without session cookie.
"""

from __future__ import annotations

import configparser

import pytest
from django.test import Client

from tests.integration.manager._setup_helpers import (
    bootstrap,
    enter_setup_mode,
    exit_setup_mode,
    patch_data_dir,
    post_json,
    reset_rate_limiter,
)


@pytest.fixture
def setup_env(mocker, tmp_path):
    enter_setup_mode(mocker)
    reset_rate_limiter(mocker)
    data_dir = patch_data_dir(mocker, tmp_path)
    yield data_dir
    exit_setup_mode()


@pytest.mark.django_db
class TestBootstrapHappyPath:

    def test_bootstrap_returns_204_and_session_cookie(self, setup_env, client):
        resp = bootstrap(client)
        assert resp.status_code == 204
        assert "sessionid" in resp.cookies

    def test_authed_mutation_succeeds_with_session(self, setup_env, client):
        assert bootstrap(client).status_code == 204
        resp2 = post_json(
            client, "/api/setup/topology/", {"topology": "manager"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "ok"

    def test_mutation_without_session_rejected(self, setup_env):
        fresh = Client()
        resp = post_json(
            fresh, "/api/setup/topology/", {"topology": "manager"},
        )
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] in (
            "setup_in_progress", "invalid_token",
        )

    def test_session_id_matches_manager_ini(self, setup_env, client):
        assert bootstrap(client).status_code == 204
        session = client.session
        assert session.get("setup_phase") is True
        sid = session.get("setup_session_id")
        cfg = configparser.ConfigParser()
        cfg.read(setup_env / "manager.ini")
        assert cfg.get("setup", "session_id") == sid

    def test_topology_checkpoint_appended(self, setup_env, client):
        assert bootstrap(client).status_code == 204
        resp = post_json(
            client, "/api/setup/topology/", {"topology": "manager"},
        )
        assert resp.status_code == 200
        from workers.services.sentinel import read_sentinel
        sentinel = read_sentinel(setup_env)
        assert sentinel is not None
        assert "topology_chosen" in sentinel.get("checkpoints", [])
