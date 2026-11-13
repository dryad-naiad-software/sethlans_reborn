# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Wizard HTTP client steps for the hand-off E2E tests.

Split from ``test_wizard_handoff.py`` to keep it under the 300-line
limit. Each helper drives one step of the wizard's HTTP API against a
:class:`conftest.DriverHandle`.
"""

from __future__ import annotations

import time

from launcher import wizard_ipc

from . import _http


# ---- URL helpers -----------------------------------------------------------

def _auth_url(handle) -> str:
    return f"{handle.wizard_base_url}/api/wizard/auth/"


def _topology_url(handle) -> str:
    return f"{handle.wizard_base_url}/api/wizard/topology/"


def _done_url(handle) -> str:
    return f"{handle.wizard_base_url}/api/wizard/done/"


def _runtime_ready_url(handle) -> str:
    return f"{handle.wizard_base_url}/api/wizard/runtime-ready/"


# ---- Test client steps -----------------------------------------------------

def open_session(handle) -> str:
    """Auth with the pinned setup token; return the issued session token."""
    status, _, parsed = _http.post_json(
        _auth_url(handle), {"token": handle.setup_token},
    )
    assert status == 200, parsed
    assert parsed and parsed.get("status") == "ok", parsed
    session = parsed.get("session_token")
    assert isinstance(session, str) and session, parsed
    return session


def pick_topology(handle, session: str, topology: str) -> None:
    status, _, parsed = _http.post_json(
        _topology_url(handle), {"topology": topology},
        headers={"X-Wizard-Session": session},
    )
    assert status == 200, parsed
    assert parsed and parsed.get("status") == "ok", parsed


def fire_done(handle, session: str) -> None:
    status, _, parsed = _http.post_json(
        _done_url(handle), {},
        headers={"X-Wizard-Session": session},
    )
    assert status == 200, parsed
    assert parsed and parsed.get("status") == "ok", parsed


def snapshot_marker_quickly(
    marker_path, ipc_secret: bytes, data_dir,
    attempts: int = 10, interval: float = 0.05,
) -> dict | None:
    """Read + validate the marker as soon as it appears, returning payload.

    Polls the file every *interval* seconds for *attempts* iterations
    (so up to ``attempts * interval`` seconds total — kept under the
    launcher's 0.5s WIZARD_DONE_POLL_INTERVAL to win the race). Returns
    None if the marker never appeared OR the launcher's poller already
    deleted it.
    """
    for _ in range(attempts):
        if marker_path.is_file():
            payload = wizard_ipc.read_marker(
                marker_path, ipc_secret,
                expected_type="wizard_done", data_dir=data_dir,
            )
            if payload is not None:
                return payload
        time.sleep(interval)
    return None


def poll_runtime_ready(
    handle, session: str | None,
    expected_status: str, timeout: float = 25.0,
) -> dict:
    """Poll /runtime-ready/ until status == *expected_status* or timeout.

    The session token is cleared from wizard memory by the /done/ handler
    (FR-W7), so passing *session=None* skips the header. The handler
    rejects unauthenticated requests with 401 — relevant for the post-
    done window where the spec acknowledges the session is gone but the
    test still wants to observe runtime-ready before driver exit.
    """
    deadline = time.monotonic() + timeout
    last: dict | None = None
    while time.monotonic() < deadline:
        headers = {"X-Wizard-Session": session} if session else {}
        status, _, parsed = _http.get_json(
            _runtime_ready_url(handle),
            headers=headers, timeout=5.0,
        )
        if status == 200 and parsed and parsed.get("status") == expected_status:
            return parsed
        last = parsed if parsed is not None else {"http_status": status}
        time.sleep(0.5)
    raise AssertionError(
        f"runtime-ready did not reach {expected_status!r} within "
        f"{timeout}s; last={last!r}"
    )


__all__ = [
    "fire_done",
    "open_session",
    "pick_topology",
    "poll_runtime_ready",
    "snapshot_marker_quickly",
]
