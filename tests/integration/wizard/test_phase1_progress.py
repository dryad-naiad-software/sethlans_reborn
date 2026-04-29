# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""End-to-end ``.setup_progress.json`` accumulation across handlers.

Covers integration-test agent's mandatory scenario 5:

* Hit several handlers in sequence and assert ``.setup_progress.json``
  accumulates the right checkpoint names in the right order.
* Idempotent re-submission to the same handler does NOT create
  duplicates (FR-CHK1).
* On POSIX, the file is ``chmod 600``.
* The persisted ``checkpoints`` list aligns with the resume-route
  walker semantics from ``checkpoints.RESUME_NEXT_ROUTE`` — the most
  recent checkpoint maps to the page the wizard would resume on
  after a re-auth.
"""

from __future__ import annotations

import json
import platform
import socket
import stat
from pathlib import Path

import pytest

from wizard.sethlans_wizard.checkpoints import (
    DATABASE_CONFIGURED,
    NETWORK_CONFIGURED,
    TOPOLOGY_CHOSEN,
    RESUME_NEXT_ROUTE,
)

from . import _http
from ._phase1_session import open_and_select, session_headers


def _bind_unused_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _read_progress(data_dir: Path) -> dict:
    target = data_dir / ".setup_progress.json"
    return json.loads(target.read_text(encoding="utf-8"))


def test_progress_accumulates_in_order_across_handlers(wizard_process):
    """network → database → admin-user adds checkpoints in append order."""
    wp = wizard_process
    session = open_and_select(wp, topology="manager")
    headers = session_headers(session)

    # 1. Network.
    free_port = _bind_unused_port()
    status, _, _ = _http.post_json(
        f"{wp.base_url}/api/wizard/network/",
        {"bind_host": "127.0.0.1", "bind_port": free_port},
        headers=headers,
    )
    assert status == 200
    payload1 = _read_progress(wp.data_dir)
    # FR-CHK4 (Spec 2): topology handler also appends topology_chosen.
    assert payload1["checkpoints"] == [
        TOPOLOGY_CHOSEN, NETWORK_CONFIGURED,
    ], payload1

    # 2. Database.
    status, _, _ = _http.post_json(
        f"{wp.base_url}/api/wizard/database/",
        {"engine": "sqlite", "name": "ordered.db"},
        headers=headers,
    )
    assert status == 200
    payload2 = _read_progress(wp.data_dir)
    assert payload2["checkpoints"] == [
        TOPOLOGY_CHOSEN, NETWORK_CONFIGURED, DATABASE_CONFIGURED,
    ], payload2

    # 3. Admin user.
    status, _, _ = _http.post_json(
        f"{wp.base_url}/api/wizard/admin-user/",
        {
            "username": "alice",
            "email": "alice@example.org",
            "password": "X9c!7Rq#Tv2pL@s",
            "password_confirm": "X9c!7Rq#Tv2pL@s",
        },
        headers=headers,
    )
    assert status == 200
    payload3 = _read_progress(wp.data_dir)
    assert payload3["checkpoints"] == [
        TOPOLOGY_CHOSEN, NETWORK_CONFIGURED, DATABASE_CONFIGURED,
        "admin_validated",
    ], payload3

    # Schema-version pin remains 1 across appends.
    assert payload3["schema_version"] == 1, payload3


def test_progress_idempotent_under_repeated_handler_post(wizard_process):
    """Re-submitting the same handler does NOT duplicate the checkpoint."""
    wp = wizard_process
    session = open_and_select(wp, topology="manager")
    headers = session_headers(session)

    free_port = _bind_unused_port()
    body = {"bind_host": "127.0.0.1", "bind_port": free_port}

    for _ in range(3):
        status, _, _ = _http.post_json(
            f"{wp.base_url}/api/wizard/network/", body, headers=headers,
        )
        assert status == 200

    payload = _read_progress(wp.data_dir)
    # Exactly one entry — re-submission is idempotent.
    assert payload["checkpoints"].count(NETWORK_CONFIGURED) == 1, payload


@pytest.mark.skipif(platform.system() == "Windows", reason="POSIX only")
def test_progress_chmod_600_on_posix(wizard_process):
    """``.setup_progress.json`` lands chmod 600 on POSIX."""
    wp = wizard_process
    session = open_and_select(wp, topology="manager")
    free_port = _bind_unused_port()
    status, _, _ = _http.post_json(
        f"{wp.base_url}/api/wizard/network/",
        {"bind_host": "127.0.0.1", "bind_port": free_port},
        headers=session_headers(session),
    )
    assert status == 200

    target = wp.data_dir / ".setup_progress.json"
    mode = target.stat().st_mode & 0o777
    assert mode == 0o600, oct(mode)
    assert not (target.stat().st_mode & stat.S_IRWXG), oct(mode)
    assert not (target.stat().st_mode & stat.S_IRWXO), oct(mode)


def test_progress_resume_route_for_latest_checkpoint(wizard_process):
    """Latest persisted checkpoint maps to the documented resume route.

    Tests the resume-walker contract end-to-end: write
    ``network_configured``, then read the file back and confirm
    ``RESUME_NEXT_ROUTE[NETWORK_CONFIGURED] == '/database'``. This
    pins the contract Phase 2's frontend re-auth bounce will rely on.
    """
    wp = wizard_process
    session = open_and_select(wp, topology="manager")
    free_port = _bind_unused_port()
    status, _, _ = _http.post_json(
        f"{wp.base_url}/api/wizard/network/",
        {"bind_host": "127.0.0.1", "bind_port": free_port},
        headers=session_headers(session),
    )
    assert status == 200

    payload = _read_progress(wp.data_dir)
    latest = payload["checkpoints"][-1]
    assert latest in RESUME_NEXT_ROUTE, latest
    assert RESUME_NEXT_ROUTE[latest] == "/database", RESUME_NEXT_ROUTE[latest]
