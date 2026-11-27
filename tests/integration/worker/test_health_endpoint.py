# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for the worker ``GET /api/health/`` endpoint.

Boots the real Waitress plaintext upstream (no Caddy in the loop --
tests target the WSGI app directly per the existing pattern in
``test_web_ui_server.py``) and exercises:

* ``/api/health/`` returns 200 with a JSON body whose keys are exactly
  ``{boot_id, worker_id, version}`` and whose values mirror
  ``runtime_state.worker_boot_id``, ``system_monitor.WORKER_ID``, and
  ``shared.version.get_version()``. Covers FR-T-3 happy path and AC-6
  (version source). ``worker_id`` null before enrollment (FR-H-7) is a
  unit case (``tests/unit/worker/web_ui/test_handlers_health.py``).

* **Soft deprecation regression guard** -- ``/api/status`` still
  returns 200, proving the FR-DEP soft deprecation did not break the
  existing dashboard's data source. Covers AC-8.

Shared bring-up helpers live in ``_health_fixtures``; the
``web_ui_served`` fixture lives in ``conftest.py`` so pytest
auto-discovers it.
"""

from sethlans_worker_agent import runtime_state, system_monitor
from shared.version import get_version

from ._health_fixtures import _get


class TestHealthEndpoint:
    """FR-T-3 happy path + AC-6."""

    def test_health_returns_200(self, web_ui_served):
        url = f"{web_ui_served['base_url']}/api/health/"
        status, _body = _get(url)
        assert status == 200

    def test_health_body_keys_are_exact(self, web_ui_served):
        url = f"{web_ui_served['base_url']}/api/health/"
        _status, body = _get(url)
        assert set(body.keys()) == {'boot_id', 'worker_id', 'version'}

    def test_health_boot_id_matches_runtime_state(self, web_ui_served):
        url = f"{web_ui_served['base_url']}/api/health/"
        _status, body = _get(url)
        # Same Python process -- handler reads runtime_state at request
        # time, so the response MUST mirror the live module attribute.
        assert body['boot_id'] == runtime_state.worker_boot_id
        assert body['boot_id']  # non-empty

    def test_health_worker_id_matches_system_monitor(self, web_ui_served):
        url = f"{web_ui_served['base_url']}/api/health/"
        _status, body = _get(url)
        # Fixture pinned WORKER_ID to a known sentinel string.
        assert body['worker_id'] == system_monitor.WORKER_ID
        assert body['worker_id'] == 'integration-worker-id-42'

    def test_health_version_matches_shared_get_version(self, web_ui_served):
        # AC-6: version equals shared.version.get_version().
        url = f"{web_ui_served['base_url']}/api/health/"
        _status, body = _get(url)
        assert body['version'] == get_version()


# --- Soft-deprecation regression guard -----------------------------

class TestStatusDeprecationRegression:
    """AC-8 + FR-DEP: ``/api/status`` still works after soft deprecation."""

    def test_status_still_returns_200_in_runtime_mode(self, web_ui_served):
        # FR-DEP-3: deprecation is documentation-only -- no behavior
        # change.  Dashboard polls /api/status and MUST keep working
        # until Spec 4 lands.
        url = f"{web_ui_served['base_url']}/api/status"
        status, body = _get(url)
        assert status == 200
        # Spot-check the snapshot shape mirrors the existing contract;
        # the full shape is exhaustively asserted by
        # tests/integration/worker/test_web_ui_server.py.
        assert 'worker' in body
        assert 'hardware' in body
        assert 'config' in body
