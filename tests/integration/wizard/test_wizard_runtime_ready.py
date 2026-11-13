# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Wizard runtime-ready endpoint integration tests (FR-W14).

Covers Spec 1 / D1 scenario 7: with no runtime listening on
``localhost:8080`` (the manager probe URL), the endpoint reports
``booting``. The ``ready`` path is deliberately deferred to D2 (E2E) —
it requires a real runtime. (The former ``.runtime_failed`` → ``failed``
scenario was removed with the marker in #208.)
"""

from __future__ import annotations

from . import _http


def _auth_url(wp) -> str:
    return f"{wp.base_url}/api/wizard/auth/"


def _topology_url(wp) -> str:
    return f"{wp.base_url}/api/wizard/topology/"


def _runtime_ready_url(wp) -> str:
    return f"{wp.base_url}/api/wizard/runtime-ready/"


def _open_session_and_pick_topology(wp, topology: str = "manager") -> str:
    """Auth + topology select; return the session token for further calls."""
    status, _, parsed = _http.post_json(
        _auth_url(wp), {"token": wp.setup_token},
    )
    assert status == 200, parsed
    session = parsed["session_token"]

    status, _, _ = _http.post_json(
        _topology_url(wp),
        {"topology": topology},
        headers={"X-Wizard-Session": session},
    )
    assert status == 200
    return session


def test_runtime_ready_reports_booting_when_no_runtime(wizard_process):
    """No runtime + no failed-marker → ``{"status": "booting", ...}``.

    The probe URL for ``manager`` topology is hardcoded to
    ``https://localhost:8080/api/health/``; nothing is listening there
    in the test harness, so the probe times out / connection-refuses
    and the handler falls through to the ``booting`` envelope.
    """
    wp = wizard_process
    session = _open_session_and_pick_topology(wp)

    status, _, parsed = _http.get_json(
        _runtime_ready_url(wp),
        headers={"X-Wizard-Session": session},
        timeout=10.0,  # probe has its own internal timeout
    )
    assert status == 200, parsed
    assert parsed and parsed.get("status") == "booting", parsed
    # ``url`` MUST be None while booting (FR-W14).
    assert parsed.get("url") is None, parsed


def test_runtime_ready_requires_session(wizard_process):
    """The endpoint rejects requests with no session header."""
    wp = wizard_process
    status, _, parsed = _http.get_json(_runtime_ready_url(wp))
    assert status == 401, parsed


def test_runtime_ready_method_not_allowed(wizard_process):
    """The endpoint only accepts GET (POST → 405)."""
    wp = wizard_process
    session = _open_session_and_pick_topology(wp)
    status, headers, _ = _http.post_json(
        _runtime_ready_url(wp),
        {},
        headers={"X-Wizard-Session": session},
    )
    assert status == 405, status
    assert headers.get("Allow") == "GET", headers


def test_runtime_ready_rejects_url_query_string(wizard_process):
    """SEC-MED-12: ``?url=...`` is forbidden (probe URL is hardcoded)."""
    wp = wizard_process
    session = _open_session_and_pick_topology(wp)
    url = _runtime_ready_url(wp) + "?url=https://evil.example/api/health/"
    status, _, parsed = _http.get_json(
        url, headers={"X-Wizard-Session": session},
    )
    assert status == 400, parsed
