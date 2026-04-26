# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Wizard auth endpoint integration tests.

Covers Spec 1 / D1 scenarios 3 (wrong-token → 403 + downstream
endpoints reject without a session) and 4 (rate-limit → 429 after
11 consecutive failed attempts from the same client). Scenario 2's
happy-path auth flow lives in :mod:`test_wizard_done_flow`.
"""

from __future__ import annotations

from . import _http


def _auth_url(wizard_process) -> str:
    return f"{wizard_process.base_url}/api/wizard/auth/"


def _topology_url(wizard_process) -> str:
    return f"{wizard_process.base_url}/api/wizard/topology/"


def test_auth_wrong_token_returns_403(wizard_process):
    """Scenario 3 (part 1): a bogus token never opens a session."""
    status, _, parsed = _http.post_json(
        _auth_url(wizard_process), {"token": "definitely-not-the-token"},
    )
    assert status == 403, parsed
    assert parsed and parsed.get("error"), parsed


def test_downstream_endpoints_require_session(wizard_process):
    """Scenario 3 (part 2): topology/done/runtime-ready reject sans session."""
    # No X-Wizard-Session header.
    status, _, parsed = _http.post_json(
        _topology_url(wizard_process), {"topology": "manager"},
    )
    assert status == 401, parsed
    # Bogus session header should also be rejected.
    status, _, parsed = _http.post_json(
        _topology_url(wizard_process),
        {"topology": "manager"},
        headers={"X-Wizard-Session": "not-a-real-session-token"},
    )
    assert status == 401, parsed


def test_auth_rate_limit_kicks_in_after_threshold(wizard_process):
    """Scenario 4: 11 consecutive failed auth attempts triggers 429.

    The wizard's rate limiter (FR-W7) caps a source IP at 10 failed
    attempts per 60-second window. Attempts 1..10 must return 403,
    attempt 11 must return 429 with a ``Retry-After`` header. We then
    do one more attempt to confirm the 429 sticks (the window does
    NOT slide forward in 100ms).
    """
    url = _auth_url(wizard_process)
    payload = {"token": "wrong-token"}
    for i in range(10):
        status, _, _ = _http.post_json(url, payload)
        assert status == 403, f"attempt {i + 1} expected 403, got {status}"

    status, headers, parsed = _http.post_json(url, payload)
    assert status == 429, parsed
    # Retry-After is mandated by the handler.
    retry = headers.get("Retry-After")
    assert retry == "60", f"expected Retry-After=60, got {retry!r}"

    # One more — should still be 429 inside the same 60s window.
    status_again, _, _ = _http.post_json(url, payload)
    assert status_again == 429, status_again


def test_auth_query_string_with_session_token_rejected(wizard_process):
    """Defense-in-depth: token-shaped query keys → 400 (FR-W-FE3a/b).

    The auth handler refuses any request whose query string carries a
    ``session_token``, ``session``, or ``token`` key — even when a
    legitimate POST body is also present.
    """
    url = _auth_url(wizard_process) + "?session_token=evil"
    status, _, parsed = _http.post_json(url, {"token": "anything"})
    assert status == 400, parsed
    assert parsed and "URL" in (parsed.get("error") or ""), parsed


def test_auth_malformed_body_returns_400(wizard_process):
    """A non-JSON body or one missing the ``token`` key → 400 (not 403)."""
    # Empty body.
    status, _, parsed = _http.request(
        "POST", _auth_url(wizard_process), body=b"",
    )
    assert status == 400, status
    # Wrong shape.
    status, _, parsed = _http.post_json(
        _auth_url(wizard_process), {"not_token": "x"},
    )
    assert status == 400, parsed


def test_auth_method_not_allowed(wizard_process):
    """The auth endpoint only accepts POST."""
    status, headers, _ = _http.get(_auth_url(wizard_process))
    assert status == 405, status
    assert headers.get("Allow") == "POST", headers
