# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the sticky-True ``is_setup_complete`` cache on the
heartbeat view (issue #130).

The heartbeat view calls a small helper, ``_is_setup_complete_cached``,
that short-circuits subsequent calls once it has observed a True from
``is_setup_complete``.  The sentinel file is monotonic over a process
lifetime (writes are atomic and the file is never deleted under normal
operation), so the cache lets steady-state heartbeats skip the
stat/read/JSON parse on every request.

These tests pin the contract:

* After the first True observation, ``is_setup_complete`` is invoked at
  most once across N heartbeats.
* Before the first True, every heartbeat re-reads — the cache only
  flips the latch once a True has been seen.
* The cache lives on the module (not on the request handler branch),
  so a True observed during full registration also covers later plain
  heartbeats.

Removing the cache (or regressing it to a plain pass-through) makes the
``call_count`` assertions fail, which is the proof the helper is doing
its job.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.models import Worker
from workers.views import heartbeat as heartbeat_mod

User = get_user_model()

HEARTBEAT_URL = '/api/heartbeat/'

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fixtures (mirror those in test_heartbeat_setup_complete.py — kept local
# rather than promoted to conftest so the two files remain independently
# readable; both are small enough that the duplication is cheaper than
# the indirection).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_heartbeat_setup_cache():
    """Reset the sticky-True setup-complete cache around every test.

    The cache lives at module scope on ``workers.views.heartbeat`` and
    survives across tests in the same process.  Reset before AND after
    so neither this file's tests nor any other file in the run can leak
    a True observation into our assertions.
    """
    heartbeat_mod._reset_setup_complete_cache()
    yield
    heartbeat_mod._reset_setup_complete_cache()


@pytest.fixture
def worker_with_token():
    """Create a Worker, linked User, and APIClient with token credentials."""
    user = User.objects.create_user(username='worker_unitcache')
    user.set_unusable_password()
    user.save()
    worker = Worker.objects.create(
        hostname='unitcache',
        user=user,
        is_active=True,
        available_tools={'blender': ['4.2.19']},
    )
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return worker, client


@pytest.fixture
def fresh_worker_client():
    """Token-authed client whose Worker row does not yet exist.

    Drives the full-registration branch — the first heartbeat from a
    freshly enrolled worker carries an ``os`` field and creates the
    Worker row.
    """
    user = User.objects.create_user(username='worker_freshcache')
    user.set_unusable_password()
    user.save()
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return 'freshcache', client


# ---------------------------------------------------------------------------
# Cache-shape tests
# ---------------------------------------------------------------------------


class TestSetupCompleteStickyCache:
    """Cache-shape tests for the sticky-True flag (issue #130)."""

    def test_sentinel_read_at_most_once_after_first_true(
        self, worker_with_token, mocker,
    ):
        """After the first True observation, no further sentinel reads.

        The cache stickies on True, so 6 heartbeats must yield exactly 1
        call to ``is_setup_complete`` and every response must report
        ``manager_setup_complete: True``.
        """
        worker, client = worker_with_token
        mock_is_complete = mocker.patch(
            'workers.views.heartbeat.is_setup_complete',
            return_value=True,
        )

        for _ in range(6):
            resp = client.post(
                HEARTBEAT_URL,
                data={'hostname': worker.hostname},
                format='json',
            )
            assert resp.status_code == 200
            assert resp.data['manager_setup_complete'] is True

        # The cache must short-circuit after the first True — proving
        # the sticky flag is in place (removing it would yield 6 calls).
        assert mock_is_complete.call_count == 1

    def test_sentinel_re_read_each_call_until_first_true(
        self, worker_with_token, mocker,
    ):
        """Before the first True, every heartbeat re-reads.

        Sequence: F, F, F, T, T, T (6 heartbeats).  The first three
        Falses each consult the sentinel; the fourth call returns True
        and stickies; the fifth and sixth short-circuit.  Total
        ``is_setup_complete`` calls: 4 (3 Falses + 1 True).
        """
        worker, client = worker_with_token
        mock_is_complete = mocker.patch(
            'workers.views.heartbeat.is_setup_complete',
            side_effect=[False, False, False, True, True, True],
        )

        expected_field = [False, False, False, True, True, True]
        for expected in expected_field:
            resp = client.post(
                HEARTBEAT_URL,
                data={'hostname': worker.hostname},
                format='json',
            )
            assert resp.status_code == 200
            assert resp.data['manager_setup_complete'] is expected

        # 3 Falses force a re-read each time; the 4th call returns True
        # and stickies, so the 5th and 6th short-circuit.  4 total.
        assert mock_is_complete.call_count == 4

    def test_cache_persists_across_full_registration_then_heartbeat(
        self, fresh_worker_client, mocker,
    ):
        """Sticky flag set during full registration also covers later
        plain heartbeats — the cache lives on the module, not on the
        request handler branch, so both code paths share it.
        """
        hostname, client = fresh_worker_client
        mock_is_complete = mocker.patch(
            'workers.views.heartbeat.is_setup_complete',
            return_value=True,
        )

        # Full registration (carries 'os') — first call stickies cache.
        resp = client.post(
            HEARTBEAT_URL,
            data={
                'hostname': hostname,
                'os': 'Linux',
                'available_tools': {'blender': ['4.2.19']},
            },
            format='json',
        )
        assert resp.status_code == 200
        assert resp.data['manager_setup_complete'] is True

        # Plain heartbeat — must short-circuit (no second sentinel read).
        for _ in range(3):
            resp = client.post(
                HEARTBEAT_URL,
                data={'hostname': hostname},
                format='json',
            )
            assert resp.status_code == 200
            assert resp.data['manager_setup_complete'] is True

        assert mock_is_complete.call_count == 1
