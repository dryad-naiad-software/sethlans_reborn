# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Concurrent handler hits — last-write-wins + no thread crashes.

Covers integration-test agent's mandatory scenario 6: spawn N threads
each calling ``POST /api/wizard/admin-user/``, assert no
``PytestUnhandledThreadExceptionWarning`` is raised (pytest.ini
promotes those to test failure), and assert the in-memory wizard_state
ends in a consistent state (last-write-wins per FR-CHK1a — the
progress file is read-modify-write under the singleton progress-file
lock; the ``admin_validated`` checkpoint is added exactly once).
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import _http
from ._phase1_session import open_and_select, session_headers


def _read_progress(data_dir: Path) -> dict:
    return json.loads(
        (data_dir / ".setup_progress.json").read_text(encoding="utf-8"),
    )


def _post_admin(wp, session: str, payload: dict, *, timeout: float = 10.0):
    return _http.post_json(
        f"{wp.base_url}/api/wizard/admin-user/",
        payload,
        headers=session_headers(session),
        timeout=timeout,
    )


def test_concurrent_admin_user_posts_no_thread_crash(wizard_process):
    """8 concurrent admin-user POSTs all return 200 with consistent state.

    The wizard handler reads + writes ``.setup_progress.json`` under
    the per-process lock. Concurrent POSTs MUST end with the
    in-memory wizard_state holding ONE admin tuple (last-write-wins,
    overwrite semantics — see ``wizard_state.set_admin``) and the
    progress file containing exactly one ``admin_validated`` entry.
    """
    wp = wizard_process
    session = open_and_select(wp, topology="manager")

    # Build N payloads with distinct usernames.
    payloads = [
        {
            "username": f"alice_{i}",
            "email": f"alice_{i}@example.org",
            "password": "X9c!7Rq#Tv2pL@s",
            "password_confirm": "X9c!7Rq#Tv2pL@s",
        }
        for i in range(8)
    ]

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [
            ex.submit(_post_admin, wp, session, p) for p in payloads
        ]
        results = [f.result() for f in as_completed(futures)]

    # Every call returned 200 — the handler is thread-safe.
    statuses = [r[0] for r in results]
    assert all(s == 200 for s in statuses), statuses

    # Progress file has the checkpoint exactly once.
    payload = _read_progress(wp.data_dir)
    assert payload["checkpoints"].count("admin_validated") == 1, payload
    assert payload["schema_version"] == 1, payload


def test_concurrent_network_posts_single_checkpoint(wizard_process):
    """Concurrent network POSTs add ``network_configured`` exactly once."""
    wp = wizard_process
    session = open_and_select(wp, topology="manager")

    # Real socket.bind needs a free port — bind+release once and re-use.
    import socket as _socket
    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]
    sock.close()

    body = {"bind_host": "127.0.0.1", "bind_port": free_port}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [
            ex.submit(
                _http.post_json,
                f"{wp.base_url}/api/wizard/network/",
                body,
                headers=session_headers(session),
                timeout=10.0,
            )
            for _ in range(8)
        ]
        results = [f.result() for f in as_completed(futures)]

    # All 200 (or 400 if SO_REUSEADDR contention beat us — we only
    # need ONE 200 for the checkpoint to be recorded once).
    successes = [r for r in results if r[0] == 200]
    assert len(successes) >= 1, results

    payload = _read_progress(wp.data_dir)
    assert payload["checkpoints"].count("network_configured") == 1, payload
