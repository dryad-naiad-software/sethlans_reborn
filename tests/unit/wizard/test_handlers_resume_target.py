# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for ``wizard/sethlans_wizard/handlers/resume_target.py``
(FR-CHK3-RESUME).

Covers:
* method gate (only GET),
* session-header gate (401),
* pre-Welcome state → ``"/"``,
* walks forward through saved checkpoints,
* manager-only topology skips ``worker_password_set``,
* all checkpoints satisfied → ``/done``,
* topology field is included in the response payload.
"""

from __future__ import annotations

from wizard.sethlans_wizard import auth_state, progress
from wizard.sethlans_wizard.checkpoints import (
    DATABASE_CONFIGURED,
    NETWORK_CONFIGURED,
    TOPOLOGY_CHOSEN,
    WELCOME_SEEN,
    ADMIN_VALIDATED,
    FFMPEG_INSTALLED,
    VERIFIED,
    WORKER_PASSWORD_SET,
)
from wizard.sethlans_wizard.handlers import resume_target as rt
from wizard.sethlans_wizard.handlers.topology import write_topology_atomic

from ._phase1_helpers import VALID_SESSION, build_environ, call_handler


def _reset():
    auth_state.reset_state_for_tests()
    auth_state.set_session_token(VALID_SESSION)


def _get_env(path="/api/wizard/resume-target/"):
    return build_environ(
        method="GET",
        path=path,
        headers={"X-Wizard-Session": VALID_SESSION},
    )


def test_post_returns_405(tmp_path):
    _reset()
    handler = rt.make_resume_target_handler(tmp_path)
    env = build_environ(
        method="POST",
        path="/api/wizard/resume-target/",
        headers={"X-Wizard-Session": VALID_SESSION},
    )
    status, headers, _ = call_handler(handler, env)
    assert status.startswith("405")
    assert headers.get("Allow") == "GET"


def test_missing_session_returns_401(tmp_path):
    _reset()
    handler = rt.make_resume_target_handler(tmp_path)
    env = build_environ(method="GET", path="/api/wizard/resume-target/")
    status, _, _ = call_handler(handler, env)
    assert status.startswith("401")


def test_pre_welcome_returns_root(tmp_path):
    _reset()
    handler = rt.make_resume_target_handler(tmp_path)
    status, _, body = call_handler(handler, _get_env())
    assert status.startswith("200"), body
    assert body["route"] == "/"
    assert body["topology"] is None


def test_after_welcome_returns_topology(tmp_path):
    _reset()
    progress.append_checkpoint(tmp_path, WELCOME_SEEN)
    handler = rt.make_resume_target_handler(tmp_path)
    status, _, body = call_handler(handler, _get_env())
    assert status.startswith("200")
    assert body["route"] == "/topology"


def test_after_database_returns_admin_user(tmp_path):
    _reset()
    write_topology_atomic(tmp_path, "manager_worker")
    for name in (WELCOME_SEEN, TOPOLOGY_CHOSEN, NETWORK_CONFIGURED, DATABASE_CONFIGURED):
        progress.append_checkpoint(tmp_path, name)
    handler = rt.make_resume_target_handler(tmp_path)
    status, _, body = call_handler(handler, _get_env())
    assert status.startswith("200")
    assert body["route"] == "/admin-user"
    assert body["topology"] == "manager_worker"


def test_manager_only_skips_worker_password(tmp_path):
    _reset()
    write_topology_atomic(tmp_path, "manager")
    for name in (
        WELCOME_SEEN, TOPOLOGY_CHOSEN, NETWORK_CONFIGURED,
        DATABASE_CONFIGURED, ADMIN_VALIDATED,
    ):
        progress.append_checkpoint(tmp_path, name)
    handler = rt.make_resume_target_handler(tmp_path)
    status, _, body = call_handler(handler, _get_env())
    assert status.startswith("200")
    # admin → ffmpeg directly because worker_password_set is skipped.
    assert body["route"] == "/ffmpeg"
    assert body["topology"] == "manager"


def test_manager_worker_visits_worker_password(tmp_path):
    _reset()
    write_topology_atomic(tmp_path, "manager_worker")
    for name in (
        WELCOME_SEEN, TOPOLOGY_CHOSEN, NETWORK_CONFIGURED,
        DATABASE_CONFIGURED, ADMIN_VALIDATED,
    ):
        progress.append_checkpoint(tmp_path, name)
    handler = rt.make_resume_target_handler(tmp_path)
    status, _, body = call_handler(handler, _get_env())
    assert status.startswith("200")
    assert body["route"] == "/worker-password"


def test_all_done_returns_done_route(tmp_path):
    _reset()
    write_topology_atomic(tmp_path, "manager_worker")
    for name in (
        WELCOME_SEEN, TOPOLOGY_CHOSEN, NETWORK_CONFIGURED,
        DATABASE_CONFIGURED, ADMIN_VALIDATED, WORKER_PASSWORD_SET,
        FFMPEG_INSTALLED, VERIFIED,
    ):
        progress.append_checkpoint(tmp_path, name)
    handler = rt.make_resume_target_handler(tmp_path)
    status, _, body = call_handler(handler, _get_env())
    assert status.startswith("200")
    assert body["route"] == "/done"


def test_compute_resume_route_function_directly(tmp_path):
    """The compute_resume_route helper is used by the WSGI handler but
    also potentially by other call sites; test it independently."""
    write_topology_atomic(tmp_path, "manager")
    progress.append_checkpoint(tmp_path, WELCOME_SEEN)
    progress.append_checkpoint(tmp_path, TOPOLOGY_CHOSEN)
    assert rt.compute_resume_route(tmp_path) == "/network"
