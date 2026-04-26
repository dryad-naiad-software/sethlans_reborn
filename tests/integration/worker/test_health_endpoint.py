# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for the worker ``GET /api/health/`` endpoint.

Boots the real Waitress plaintext upstream (no Caddy in the loop --
tests target the WSGI app directly per the existing pattern in
``test_web_ui_server.py``) and exercises:

* **Setup-complete fixture variant** -- gate is open via
  ``mark_setup_complete``; ``/api/health/`` returns 200 with a JSON
  body whose keys are exactly ``{boot_id, worker_id, version}`` and
  whose values mirror ``runtime_state.worker_boot_id``,
  ``system_monitor.WORKER_ID``, and ``shared.version.get_version()``.
  Covers FR-T-3 happy path and AC-6 (version source).

* **Sentinel-absent fixture variant** -- gate is closed
  (``mark_setup_complete`` deliberately *not* called; an empty data
  directory is supplied to ``init_gate`` to scrub any module-level
  bleed-through from sibling tests). ``/api/health/`` MUST still
  return 200 with ``worker_id: null`` (FR-W-2 / FR-H-7), while
  ``/api/status`` returns 503 from the gate -- the paired assertion
  proves we are exercising the allowlist, not the open-gate fast path.
  Covers FR-T-3 sentinel-absent variant, AC-7, and DevOps-LOW-5.

* **Soft deprecation regression guard** -- in the setup-complete
  variant, ``/api/status`` still returns 200, proving the FR-DEP soft
  deprecation did not break the existing dashboard's data source.
  Covers AC-8.

Shared bring-up helpers live in ``_health_fixtures``; the two
``web_ui_setup_*`` fixtures live in ``conftest.py`` so pytest
auto-discovers them.  Both moves keep this file under the 300-line
limit.
"""

from sethlans_worker_agent import runtime_state, system_monitor
from shared.version import get_version

from ._health_fixtures import _get


# --- Setup-complete variant tests ----------------------------------

class TestHealthEndpointSetupComplete:
    """FR-T-3 happy path + AC-6: setup-complete fixture variant."""

    def test_health_returns_200(self, web_ui_setup_complete):
        url = f"{web_ui_setup_complete['base_url']}/api/health/"
        status, _body = _get(url)
        assert status == 200

    def test_health_body_keys_are_exact(self, web_ui_setup_complete):
        url = f"{web_ui_setup_complete['base_url']}/api/health/"
        _status, body = _get(url)
        assert set(body.keys()) == {'boot_id', 'worker_id', 'version'}

    def test_health_boot_id_matches_runtime_state(
        self, web_ui_setup_complete,
    ):
        url = f"{web_ui_setup_complete['base_url']}/api/health/"
        _status, body = _get(url)
        # Same Python process -- handler reads runtime_state at request
        # time, so the response MUST mirror the live module attribute.
        assert body['boot_id'] == runtime_state.worker_boot_id
        assert body['boot_id']  # non-empty

    def test_health_worker_id_matches_system_monitor(
        self, web_ui_setup_complete,
    ):
        url = f"{web_ui_setup_complete['base_url']}/api/health/"
        _status, body = _get(url)
        # Fixture pinned WORKER_ID to a known sentinel string.
        assert body['worker_id'] == system_monitor.WORKER_ID
        assert body['worker_id'] == 'integration-worker-id-42'

    def test_health_version_matches_shared_get_version(
        self, web_ui_setup_complete,
    ):
        # AC-6: version equals shared.version.get_version().
        url = f"{web_ui_setup_complete['base_url']}/api/health/"
        _status, body = _get(url)
        assert body['version'] == get_version()


# --- Sentinel-absent variant tests ---------------------------------

class TestHealthEndpointSetupPending:
    """FR-T-3 sentinel-absent + AC-7 + DevOps-LOW-5."""

    def test_health_returns_200_when_setup_incomplete(
        self, web_ui_setup_pending,
    ):
        # FR-W-2 + AC-7: the allowlist keeps /api/health/ reachable
        # even though the setup sentinel has not been written.
        url = f"{web_ui_setup_pending['base_url']}/api/health/"
        status, _body = _get(url)
        assert status == 200

    def test_health_worker_id_is_null_pre_enrollment(
        self, web_ui_setup_pending,
    ):
        # FR-H-7: pre-enrollment, worker_id MUST serialize as JSON null.
        url = f"{web_ui_setup_pending['base_url']}/api/health/"
        _status, body = _get(url)
        assert body['worker_id'] is None

    def test_health_body_keys_are_exact_pre_enrollment(
        self, web_ui_setup_pending,
    ):
        url = f"{web_ui_setup_pending['base_url']}/api/health/"
        _status, body = _get(url)
        assert set(body.keys()) == {'boot_id', 'worker_id', 'version'}

    def test_status_returns_503_when_setup_incomplete(
        self, web_ui_setup_pending,
    ):
        # AC-7 paired assertion: a non-allowlisted route MUST be 503.
        # Without this, a green /api/health/ test could be silently
        # exercising the open-gate fast path instead of the allowlist.
        url = f"{web_ui_setup_pending['base_url']}/api/status"
        status, body = _get(url)
        assert status == 503
        # Sanity: confirm the 503 came from the setup gate, not from
        # some other layer (e.g. shutdown-in-progress 503).
        assert body.get('detail') == 'Setup not complete.'


# --- Soft-deprecation regression guard -----------------------------

class TestStatusDeprecationRegression:
    """AC-8 + FR-DEP: ``/api/status`` still works after soft deprecation."""

    def test_status_still_returns_200_in_runtime_mode(
        self, web_ui_setup_complete,
    ):
        # FR-DEP-3: deprecation is documentation-only -- no behavior
        # change.  Dashboard polls /api/status at index.html:279 and
        # MUST keep working until Spec 4 lands.
        url = f"{web_ui_setup_complete['base_url']}/api/status"
        status, body = _get(url)
        assert status == 200
        # Spot-check the snapshot shape mirrors the existing contract;
        # the full shape is exhaustively asserted by
        # tests/integration/worker/test_web_ui_server.py.
        assert 'worker' in body
        assert 'hardware' in body
        assert 'config' in body
