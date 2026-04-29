# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Shared session/auth helpers for Phase 1 (Spec 2) integration tests.

The Phase 1 step handlers all require a valid ``X-Wizard-Session``
header; opening a session means POSTing the setup token to
``/api/wizard/auth/`` and stashing the issued ``session_token``.
Every Phase 1 integration test starts with the same boilerplate, so
collecting it here keeps each test focused on the assertion under
test rather than the auth handshake.
"""

from __future__ import annotations

from . import _http


def open_session(wp) -> str:
    """POST the setup token; return the issued session token."""
    status, _, parsed = _http.post_json(
        f"{wp.base_url}/api/wizard/auth/",
        {"token": wp.setup_token},
    )
    assert status == 200, parsed
    assert parsed and parsed.get("status") == "ok", parsed
    session_token = parsed.get("session_token")
    assert isinstance(session_token, str) and session_token, parsed
    return session_token


def select_topology(wp, session: str, topology: str = "manager") -> None:
    """POST topology selection. Asserts 200."""
    status, _, parsed = _http.post_json(
        f"{wp.base_url}/api/wizard/topology/",
        {"topology": topology},
        headers={"X-Wizard-Session": session},
    )
    assert status == 200, parsed


def open_and_select(wp, topology: str = "manager") -> str:
    """One-shot: open session + select topology; return the session."""
    session = open_session(wp)
    select_topology(wp, session, topology=topology)
    return session


def session_headers(session: str) -> dict[str, str]:
    """Return the canonical ``X-Wizard-Session`` headers dict."""
    return {"X-Wizard-Session": session}


def session_cookie_header(session: str) -> dict[str, str]:
    """Return a ``Cookie: wizard_session=...`` header dict (issue #175).

    Page routes are gated by the wizard_session cookie (the API uses
    the X-Wizard-Session header). Tests that GET a page after auth
    must pass the cookie via this helper; passing the header alone
    will trigger the 302-to-/token gate.
    """
    return {"Cookie": f"wizard_session={session}"}


__all__ = [
    "open_session",
    "select_topology",
    "open_and_select",
    "session_headers",
    "session_cookie_header",
]
