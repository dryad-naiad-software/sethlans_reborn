# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for issue #215: ``POST /api/heartbeat/`` must bind the
target worker to the authenticated caller.

A worker token may only heartbeat or re-register its own ``Worker`` row.
A body ``hostname`` naming another worker, an unknown host, or a renamed
machine is rejected with 403 and nothing is created or changed.  The
non-worker-caller and legitimate-flow cases live in
``test_heartbeat_binding_flows.py``.
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.models import Worker
from workers.views import heartbeat_auth

from .heartbeat_binding_helpers import (
    FULL_REGISTRATION,
    HEARTBEAT_URL,
    make_two_workers,
    snapshot,
)

User = get_user_model()


@pytest.fixture
def two_workers(db, default_version):
    """Two enrolled workers, A (``host-a``) and B (``host-b``)."""
    return make_two_workers()


@pytest.mark.django_db
class TestCrossWorkerImpersonation:
    """Worker A's token posting worker B's hostname is rejected."""

    def test_plain_heartbeat_for_other_worker_is_forbidden(self, two_workers):
        (_, _, client_a), (worker_b, token_b, _) = two_workers
        before = snapshot(worker_b)

        resp = client_a.post(
            HEARTBEAT_URL,
            data={'hostname': 'host-b', 'status': 'RENDERING',
                  'ui_url': 'https://evil.example:7443'},
            format='json',
        )

        assert resp.status_code == 403
        assert snapshot(worker_b) == before
        assert Token.objects.get(user=worker_b.user).key == token_b.key

    def test_full_registration_for_other_worker_is_forbidden(
        self, two_workers,
    ):
        (_, token_a, client_a), (worker_b, token_b, _) = two_workers
        before = snapshot(worker_b)
        user_count = User.objects.count()

        resp = client_a.post(
            HEARTBEAT_URL,
            data={'hostname': 'host-b', **FULL_REGISTRATION},
            format='json',
        )

        assert resp.status_code == 403
        body = resp.content.decode()
        assert token_b.key not in body
        assert token_a.key not in body
        assert snapshot(worker_b) == before
        assert Token.objects.get(user=worker_b.user).key == token_b.key
        assert User.objects.count() == user_count

    def test_rejection_is_logged_without_secrets(self, two_workers, mocker):
        (worker_a, token_a, client_a), (_, token_b, _) = two_workers
        # The project LOGGING config does not propagate ``workers.*`` to
        # the root logger, so patch the module logger instead of caplog.
        warn = mocker.patch.object(heartbeat_auth.logger, 'warning')

        client_a.post(
            HEARTBEAT_URL,
            data={'hostname': 'host-b', **FULL_REGISTRATION},
            format='json',
        )

        messages = [c.args[0] % c.args[1:] for c in warn.call_args_list]
        assert any('host-b' in m and str(worker_a.pk) in m for m in messages)
        joined = '\n'.join(messages)
        assert token_a.key not in joined
        assert token_b.key not in joined


@pytest.mark.django_db
class TestUnknownHostnameCreatesNothing:
    """A valid worker token naming an unknown hostname creates nothing."""

    @pytest.mark.parametrize('extra', [{}, FULL_REGISTRATION])
    def test_unknown_hostname_is_forbidden(self, two_workers, extra):
        (worker_a, _, client_a), _ = two_workers
        before_a = snapshot(worker_a)
        user_count = User.objects.count()
        token_count = Token.objects.count()

        resp = client_a.post(
            HEARTBEAT_URL,
            data={'hostname': 'brand-new-host', **extra},
            format='json',
        )

        assert resp.status_code == 403
        assert not Worker.objects.filter(hostname='brand-new-host').exists()
        assert not User.objects.filter(
            username__startswith='worker_brand-new-host',
        ).exists()
        assert User.objects.count() == user_count
        assert Token.objects.count() == token_count
        assert snapshot(worker_a) == before_a

    def test_renamed_machine_gets_reenroll_message(self, two_workers):
        """The caller's own token with a new hostname is told to re-enroll."""
        (_, _, client_a), _ = two_workers

        resp = client_a.post(
            HEARTBEAT_URL,
            data={'hostname': 'host-a-renamed', **FULL_REGISTRATION},
            format='json',
        )

        assert resp.status_code == 403
        assert 're-enroll' in resp.data['detail']
        assert not Worker.objects.filter(hostname='host-a-renamed').exists()

    def test_token_without_worker_row_cannot_register(self, default_version):
        """A token whose user has no Worker row cannot conjure one."""
        user = User.objects.create_user(username='worker_orphan')
        token = Token.objects.create(user=user)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

        resp = client.post(
            HEARTBEAT_URL,
            data={'hostname': 'orphan', **FULL_REGISTRATION},
            format='json',
        )

        assert resp.status_code == 403
        assert not Worker.objects.filter(hostname='orphan').exists()


@pytest.mark.django_db
class TestHostnameMatchIsExact:
    """Near-miss spellings of the caller's own hostname are rejected.

    Guards against a future ``iexact``/strip/Unicode-normalised lookup,
    which would let a caller address a row it does not own.
    """

    @pytest.mark.parametrize('hostname', [
        'HOST-A',
        'host-a ',
        'host-ａ',  # fullwidth latin small letter a
    ])
    @pytest.mark.parametrize('extra', [{}, FULL_REGISTRATION])
    def test_variant_hostname_is_forbidden(self, two_workers, hostname, extra):
        (worker_a, _, client_a), (worker_b, _, _) = two_workers
        before = {w.pk: snapshot(w) for w in (worker_a, worker_b)}
        worker_count = Worker.objects.count()

        resp = client_a.post(
            HEARTBEAT_URL,
            data={'hostname': hostname, **extra},
            format='json',
        )

        assert resp.status_code == 403
        assert Worker.objects.count() == worker_count
        for worker in (worker_a, worker_b):
            assert snapshot(worker) == before[worker.pk]


@pytest.mark.django_db
class TestNonObjectBody:
    """A JSON body that is not an object is a 400, not a 500."""

    @pytest.mark.parametrize('body', [['host-a'], 'host-a', 42])
    def test_worker_non_object_body_is_400(self, two_workers, body):
        (worker_a, _, client_a), _ = two_workers
        before = snapshot(worker_a)

        resp = client_a.post(HEARTBEAT_URL, data=body, format='json')

        assert resp.status_code == 400
        assert resp.data['detail'] == 'Expected a JSON object.'
        assert snapshot(worker_a) == before

    def test_non_worker_non_object_body_is_still_403(self, admin_client):
        resp = admin_client.post(HEARTBEAT_URL, data=['x'], format='json')
        assert resp.status_code == 403
