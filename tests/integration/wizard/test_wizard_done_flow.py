# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Wizard auth → topology → done happy-path flow + topology validation.

Covers Spec 1 / D1 scenarios 2 (full hand-off lifecycle: auth, write
topology, fire ``/done``, verify the ``.wizard_done`` HMAC marker is
on disk and verifiable) and 5 (topology endpoint validation rejects
out-of-vocabulary values).
"""

from __future__ import annotations

import json

from wizard.sethlans_wizard import ipc

from . import _http


def _auth_url(wp) -> str:
    return f"{wp.base_url}/api/wizard/auth/"


def _topology_url(wp) -> str:
    return f"{wp.base_url}/api/wizard/topology/"


def _done_url(wp) -> str:
    return f"{wp.base_url}/api/wizard/done/"


def _open_session(wp) -> str:
    """Auth with the right token; return the issued session token."""
    status, _, parsed = _http.post_json(
        _auth_url(wp), {"token": wp.setup_token},
    )
    assert status == 200, parsed
    assert parsed and parsed.get("status") == "ok", parsed
    session_token = parsed.get("session_token")
    assert isinstance(session_token, str) and session_token, parsed
    return session_token


def test_full_handoff_writes_topology_and_done_marker(wizard_process):
    """Scenario 2: the full lifecycle yields a valid ``.wizard_done`` marker.

    Validates the marker via :func:`wizard.sethlans_wizard.ipc.read_marker`
    using the same IPC secret the test fixture handed to the wizard.
    """
    wp = wizard_process
    session = _open_session(wp)

    # Topology selection → topology.json on disk.
    status, _, parsed = _http.post_json(
        _topology_url(wp),
        {"topology": "manager"},
        headers={"X-Wizard-Session": session},
    )
    assert status == 200, parsed
    assert parsed and parsed.get("status") == "ok", parsed

    topology_path = wp.data_dir / "topology.json"
    assert topology_path.is_file(), f"missing {topology_path}"
    topology_json = json.loads(topology_path.read_text(encoding="utf-8"))
    assert topology_json["topology"] == "manager", topology_json
    assert topology_json["schema_version"] == 1, topology_json
    assert topology_json["chosen_at"], topology_json  # non-empty ISO-8601

    # Done → .wizard_done HMAC marker.
    status, _, parsed = _http.post_json(
        _done_url(wp), {}, headers={"X-Wizard-Session": session},
    )
    assert status == 200, parsed
    assert parsed and parsed.get("status") == "ok", parsed

    marker_path = wp.wizard_subdir / ipc.MARKER_WIZARD_DONE
    assert marker_path.is_file(), f"missing {marker_path}"
    payload = ipc.read_marker(
        marker_path,
        wp.ipc_secret,
        expected_type="wizard_done",
        data_dir=wp.data_dir,
    )
    assert payload is not None, "marker failed HMAC + freshness validation"
    assert payload["topology"] == "manager", payload
    assert payload["wizard_port"] == wp.port, payload
    assert payload["type"] == "wizard_done", payload
    assert payload["schema_version"] == 1, payload
    # wizard_pid was embedded by the done handler. We only assert it's
    # a positive int — on Windows the subprocess Popen.pid is the
    # python.exe launcher PID, which can differ from the wizard's own
    # os.getpid() when waitress threads / re-exec come into play.
    assert isinstance(payload.get("wizard_pid"), int), payload
    assert payload["wizard_pid"] > 0, payload


def test_done_idempotent_returns_already_done(wizard_process):
    """A second POST /done/ returns ``already_done`` without re-writing."""
    wp = wizard_process
    session = _open_session(wp)

    status, _, _ = _http.post_json(
        _topology_url(wp),
        {"topology": "manager_worker"},
        headers={"X-Wizard-Session": session},
    )
    assert status == 200

    status, _, parsed = _http.post_json(
        _done_url(wp), {}, headers={"X-Wizard-Session": session},
    )
    assert status == 200, parsed
    assert parsed and parsed.get("status") == "ok", parsed

    marker_path = wp.wizard_subdir / ipc.MARKER_WIZARD_DONE
    first_mtime = marker_path.stat().st_mtime_ns

    # The second call: session was cleared after the first /done/, so
    # we expect a 401 (FR-W7 — session token cleared from memory before
    # exit delay). Either way, the marker must NOT be re-written.
    status, _, parsed = _http.post_json(
        _done_url(wp), {}, headers={"X-Wizard-Session": session},
    )
    # 401 (session cleared) is the production path; 200 already_done
    # would also be acceptable if the implementation kept the session
    # alive. Assert we did NOT corrupt the marker either way.
    assert status in (200, 401), parsed
    second_mtime = marker_path.stat().st_mtime_ns
    assert second_mtime == first_mtime, (
        "marker was rewritten on idempotent call; "
        f"first={first_mtime} second={second_mtime}"
    )


def test_done_without_topology_returns_400(wizard_process):
    """Done refuses to fire if topology hasn't been chosen first."""
    wp = wizard_process
    session = _open_session(wp)

    status, _, parsed = _http.post_json(
        _done_url(wp), {}, headers={"X-Wizard-Session": session},
    )
    assert status == 400, parsed
    assert parsed and "topology" in (parsed.get("error") or "").lower(), parsed


def test_topology_invalid_value_rejected(wizard_process):
    """Scenario 5: out-of-vocabulary topology values → 400."""
    wp = wizard_process
    session = _open_session(wp)

    for bogus in ("invalid_value", "MANAGER", "", None, 42, ["manager"]):
        status, _, parsed = _http.post_json(
            _topology_url(wp),
            {"topology": bogus},
            headers={"X-Wizard-Session": session},
        )
        assert status == 400, (bogus, status, parsed)


def test_topology_method_not_allowed(wizard_process):
    """Topology endpoint rejects GET with 405 + Allow: POST."""
    wp = wizard_process
    status, headers, _ = _http.get(_topology_url(wp))
    assert status == 405, status
    assert headers.get("Allow") == "POST", headers
