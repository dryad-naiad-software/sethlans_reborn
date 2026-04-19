# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Multi-tab race integration (FR-4a / C3 / F10).

Two clients both bootstrap with the same valid token — the first
session is bound to ``manager.ini [setup] session_id``; the second
session's mutating calls must be rejected with
``setup_session_conflict`` 409.
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
class TestMultiTabRace:

    def test_both_tabs_get_204_and_distinct_cookies(self, setup_env):
        tab_a, tab_b = Client(), Client()
        resp_a = bootstrap(tab_a)
        resp_b = bootstrap(tab_b)
        assert resp_a.status_code == 204
        assert resp_b.status_code == 204
        sid_a = tab_a.session.get("setup_session_id")
        sid_b = tab_b.session.get("setup_session_id")
        assert sid_a and sid_b
        assert sid_a != sid_b

    def test_ini_bound_to_first_bootstrap(self, setup_env):
        tab_a, tab_b = Client(), Client()
        assert bootstrap(tab_a).status_code == 204
        # Read the session_id bound by tab A BEFORE tab B bootstraps.
        cfg = configparser.ConfigParser()
        cfg.read(setup_env / "manager.ini")
        bound_after_a = cfg.get("setup", "session_id")
        assert bound_after_a == tab_a.session.get("setup_session_id")

        assert bootstrap(tab_b).status_code == 204
        cfg.read(setup_env / "manager.ini")
        bound_after_b = cfg.get("setup", "session_id")
        # Unchanged — still tab A's session_id.
        assert bound_after_b == bound_after_a
        assert bound_after_b != tab_b.session.get("setup_session_id")

    def test_first_tab_can_mutate(self, setup_env):
        tab_a = Client()
        assert bootstrap(tab_a).status_code == 204
        resp = post_json(
            tab_a, "/api/setup/topology/", {"topology": "manager"},
        )
        assert resp.status_code == 200

    def test_second_tab_mutation_rejected_409(self, setup_env):
        tab_a, tab_b = Client(), Client()
        assert bootstrap(tab_a).status_code == 204
        assert bootstrap(tab_b).status_code == 204

        resp = post_json(
            tab_b, "/api/setup/topology/", {"topology": "manager"},
        )
        # Spec FR-4a / C3: mutation from the non-bound tab must return
        # 409 with the ``setup_session_conflict`` envelope code.  The
        # permission class answers only "is this a setup-phase session
        # at all?"; the binding check is performed by the enforcement
        # helper at the top of each mutating view.
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"]["code"] == "setup_session_conflict"
