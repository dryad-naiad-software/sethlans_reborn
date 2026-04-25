# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration test for the wire contract added by issue #126.

The manager surfaces ``manager_setup_complete`` on every heartbeat
response so workers can self-gate on the setup-wizard state (skip
Blender downloads / job-claim while False).

Unit tests on both sides cover:

* the manager-side sentinel-to-response mapping
  (``tests/unit/manager/test_heartbeat_setup_complete.py``), and
* the worker-side response-to-state mapping plus loop gating
  (``tests/unit/worker/test_system_monitor_setup_complete.py`` and
  siblings).

What's missing — and what this file locks in — is the field-name and
field-value contract end-to-end through a real ``APIClient`` request:
the same heartbeat URL the worker hits, the same TokenAuthentication,
the same response envelope the worker parses.  Walking the False ->
True transition in a single test mirrors the actual sequence the worker
will observe in the field (operator finishes the wizard mid-loop) and
catches both the "field missing" and "value never updates" regressions
in one go.
"""

from __future__ import annotations

import pytest

from workers.services.sentinel import create_sentinel, write_sentinel
from workers.views import heartbeat as heartbeat_mod

HEARTBEAT_URL = '/api/heartbeat/'


@pytest.mark.django_db
class TestHeartbeatSetupCompleteRoundtrip:
    """Heartbeat surfaces ``manager_setup_complete`` reflecting the sentinel."""

    def test_field_flips_false_to_true_across_two_heartbeats(
        self, worker_with_token, mocker, tmp_path,
    ):
        """The worker observes the wizard finishing mid-session.

        First heartbeat: a mid-wizard sentinel exists (``completed_at``
        is None) — manager must report False.  Operator finishes the
        wizard (we call ``create_sentinel``, which stamps a real
        ``completed_at``).  Second heartbeat: same worker, same client,
        same URL — manager must now report True.

        The data-dir override target is ``workers.views.heartbeat.
        _get_data_dir`` (the local binding the view actually calls) —
        same patch site the unit tests use, kept consistent so a
        rename surfaces in both layers.
        """
        worker, client = worker_with_token
        mocker.patch.object(
            heartbeat_mod, '_get_data_dir', return_value=tmp_path,
        )

        # --- Act 1: mid-wizard sentinel -> field must be False ----------
        write_sentinel(tmp_path, {
            'version': 1,
            'completed_at': None,
            'topology': 'manager',
            'checkpoints': ['topology_chosen'],
        })

        resp = client.post(
            HEARTBEAT_URL,
            data={'hostname': worker.hostname},
            format='json',
        )
        assert resp.status_code == 200
        assert 'manager_setup_complete' in resp.data, (
            "Manager must always include manager_setup_complete on "
            "heartbeat responses (issue #126); workers default to True "
            "on missing field, which would silently mask the bug here."
        )
        assert resp.data['manager_setup_complete'] is False

        # --- Act 2: wizard finalized -> field must be True --------------
        # create_sentinel writes a sentinel with a fresh ISO timestamp
        # in ``completed_at`` — the canonical "setup done" state that
        # ``is_setup_complete`` reads each call (no caching).
        create_sentinel(
            tmp_path, 'manager', ['topology_chosen', 'verified'],
        )

        resp = client.post(
            HEARTBEAT_URL,
            data={'hostname': worker.hostname},
            format='json',
        )
        assert resp.status_code == 200
        assert resp.data['manager_setup_complete'] is True
