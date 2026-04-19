# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``SetupGateMiddleware`` allowlist regression for the tray status path.

Background:
    The tray helper's loopback status listener shares Django's middleware
    stack with the main HTTPS listener.  If ``/api/status/public/`` were
    not in the gate's allowlist, a probe that landed on the main listener
    during setup mode would return a 403 ``setup_in_progress`` envelope
    instead of letting Django's URL resolver answer with the correct 404
    (path not registered on the main URLconf).  Much worse, the same
    middleware stack is applied to the loopback listener — a non-
    allowlisted status path would 403 there too, silently breaking the
    tray.

These tests exercise only the middleware behaviour:

* During setup mode, ``/api/status/public/`` bypasses the gate — the
  request passes through to the URL resolver (main listener → 404; in
  this test the Django test client uses ROOT_URLCONF=main, so 404 is the
  expected end state).
* A near-miss path (``/api/status/publicX``) does NOT bypass the gate —
  the prefix match is exact on the trailing slash, so this returns the
  regular 403 ``setup_in_progress`` envelope.
"""

from __future__ import annotations

import pytest

from sethlans_manager.middleware import setup_gate


@pytest.fixture
def _enter_setup_mode(mocker):
    prev = setup_gate._setup_complete
    setup_gate._setup_complete = False
    mocker.patch.object(setup_gate, "_check_sentinel", return_value=False)
    yield
    setup_gate._setup_complete = prev


@pytest.mark.django_db
class TestSetupGateStatusPublicAllowlist:

    def test_status_public_bypasses_gate_in_setup_mode(
        self, _enter_setup_mode, client,
    ):
        # The main URLconf does not register /api/status/public/, so the
        # allowlist-passthrough lands at Django's URL resolver which
        # returns 404.  The critical negative: it is NOT the gate's
        # 403 ``setup_in_progress`` envelope.
        resp = client.get("/api/status/public/")
        assert resp.status_code == 404
        # Body must not be the setup-in-progress envelope from the
        # middleware.  Either the SPA catch-all responds (HTML) or
        # Django's bare 404 page does; neither is the JSON envelope.
        content_type = resp.get("Content-Type", "")
        if "application/json" in content_type:
            body = resp.json()
            if "error" in body:
                assert body["error"].get("code") != "setup_in_progress"

    def test_prefix_collision_not_allowlisted(
        self, _enter_setup_mode, client,
    ):
        # ``/api/status/publicX`` must NOT be treated as allowlisted —
        # the prefix rule is exact-trailing-slash.  Since it starts with
        # /api/ and is unlisted, the gate returns 403 ``setup_in_progress``.
        resp = client.get("/api/status/publicX")
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "setup_in_progress"

    def test_gate_still_blocks_other_api_paths(
        self, _enter_setup_mode, client,
    ):
        # Control: a random /api/ path still gets blocked by the gate,
        # confirming the allowlist additions haven't widened scope.
        resp = client.get("/api/projects/")
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "setup_in_progress"
