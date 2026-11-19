# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``POST /api/heartbeat/{pk}/dismiss-card-alert/``
(worker card selection FR-CR-5, AC-16): admin only, the id must name an
alert the worker reported, appended under the row lock, every non-2xx
answer is ``{"detail": ...}``.
"""

import pytest
from rest_framework.test import APIClient

from tests.unit.manager.card_report_helpers import ALERT_ID, ALERT_ID_2, alert, stored
from workers.models import Worker
from workers.views import card_actions

from .token_admin_helpers import session_client

STATUS = stored(alerts=[alert(), alert(id=ALERT_ID_2, kind='card_changed')])


def _url(pk):
    return f'/api/heartbeat/{pk}/dismiss-card-alert/'


@pytest.fixture
def reported(make_worker_client):
    """A worker whose stored report has two alerts: ``(worker, worker client)``."""
    worker, client, _token = make_worker_client('dismiss-host')
    Worker.objects.filter(pk=worker.pk).update(card_status=STATUS)
    return worker, client


def _dismissals(worker):
    return Worker.objects.get(pk=worker.pk).card_alert_dismissals


@pytest.mark.django_db
class TestDismissAllowed:

    def test_admin_dismisses_a_reported_alert(self, admin_client, admin_user, reported, mocker):
        worker, _client = reported
        info = mocker.patch.object(card_actions.logger, 'info')
        resp = admin_client.post(_url(worker.pk), {'alert_id': ALERT_ID}, format='json')
        assert resp.status_code == 200
        assert resp.data == {'card_status': STATUS, 'card_alert_dismissals': [ALERT_ID]}
        assert _dismissals(worker) == [ALERT_ID]
        line = info.call_args.args[0] % info.call_args.args[1:]
        assert line == f'Card alert {ALERT_ID} on worker dismiss-host dismissed by user id={admin_user.pk}'

    def test_repeat_adds_no_duplicate(self, admin_client, reported):
        worker, _client = reported
        for _ in range(2):
            resp = admin_client.post(_url(worker.pk), {'alert_id': ALERT_ID}, format='json')
        assert resp.status_code == 200
        assert _dismissals(worker) == [ALERT_ID]

    def test_second_alert_is_appended(self, admin_client, reported):
        worker, _client = reported
        admin_client.post(_url(worker.pk), {'alert_id': ALERT_ID}, format='json')
        resp = admin_client.post(_url(worker.pk), {'alert_id': ALERT_ID_2}, format='json')
        assert resp.data['card_alert_dismissals'] == [ALERT_ID, ALERT_ID_2]

    def test_stale_ids_are_pruned_and_the_list_stays_within_sixteen(self, admin_client, reported):
        worker, _client = reported
        stale = [f'{n:032x}' for n in range(20)]
        Worker.objects.filter(pk=worker.pk).update(card_alert_dismissals=stale)
        resp = admin_client.post(_url(worker.pk), {'alert_id': ALERT_ID}, format='json')
        assert resp.data['card_alert_dismissals'] == [ALERT_ID]
        assert _dismissals(worker) == [ALERT_ID]

    def test_staff_session_through_real_login(self, reported):
        worker, _client = reported
        client = session_client('staff-dismiss', 'pw-12345678', is_staff=True)
        assert client.post(_url(worker.pk), {'alert_id': ALERT_ID}, format='json').status_code == 200


@pytest.mark.django_db
class TestDismissRefused:

    def test_worker_token_gets_403(self, reported):
        worker, client = reported
        resp = client.post(_url(worker.pk), {'alert_id': ALERT_ID}, format='json')
        assert resp.status_code == 403
        assert set(resp.data) == {'detail'}
        assert _dismissals(worker) == []

    def test_non_staff_user_gets_403(self, reported):
        worker, _client = reported
        client = session_client('plain-user', 'pw-12345678', is_staff=False)
        resp = client.post(_url(worker.pk), {'alert_id': ALERT_ID}, format='json')
        assert resp.status_code == 403
        assert set(resp.data) == {'detail'}

    def test_anonymous_gets_401(self, reported):
        worker, _client = reported
        resp = APIClient().post(_url(worker.pk), {'alert_id': ALERT_ID}, format='json')
        assert resp.status_code == 401
        assert set(resp.data) == {'detail'}
        assert _dismissals(worker) == []

    @pytest.mark.parametrize('body', [
        {'alert_id': 'c' * 32}, {'alert_id': 'A' * 32}, {'alert_id': 'a' * 31},
        {'alert_id': 7}, {'alert_id': None}, {}, ['a' * 32],
    ])
    def test_bad_or_unreported_id_gets_400(self, admin_client, reported, body):
        worker, _client = reported
        resp = admin_client.post(_url(worker.pk), body, format='json')
        assert resp.status_code == 400
        assert set(resp.data) == {'detail'}
        assert _dismissals(worker) == []

    def test_worker_without_a_report_gets_400(self, admin_client, make_worker_client):
        worker, _client, _token = make_worker_client('no-report')
        resp = admin_client.post(_url(worker.pk), {'alert_id': ALERT_ID}, format='json')
        assert resp.status_code == 400
        assert resp.data == {'detail': card_actions.NOT_REPORTED_DETAIL}

    @pytest.mark.parametrize('pk', ['999999', 'abc', '1' * 20])
    def test_unknown_worker_gets_404(self, admin_client, reported, pk):
        resp = admin_client.post(_url(pk), {'alert_id': ALERT_ID}, format='json')
        assert resp.status_code == 404
        assert resp.data == {'detail': 'Worker not found.'}

    def test_get_is_not_allowed(self, admin_client, reported):
        worker, _client = reported
        resp = admin_client.get(_url(worker.pk))
        assert resp.status_code == 405
        assert set(resp.data) == {'detail'}
