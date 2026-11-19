# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the ``cards`` report in registration and heartbeats,
and the dismissal round trip (worker card selection FR-CR-2..4, FR-CR-7,
D24, AC-15, AC-17, AC-35 for ``card_status``).
"""

import pytest
from django.db import DatabaseError, connection
from django.test.utils import CaptureQueriesContext

from tests.unit.manager.card_report_helpers import ALERT_ID, ALERT_ID_2, alert, report, stored
from workers.models import Worker
from workers.views import heartbeat as heartbeat_mod
from workers.views import heartbeat_card_sync

HB = '/api/heartbeat/'
TWO_ALERTS = [alert(), alert(id=ALERT_ID_2, kind='card_changed')]


def _post(client, hostname, **extra):
    resp = client.post(HB, {'hostname': hostname, **extra}, format='json')
    assert resp.status_code == 200, resp.data
    return resp


def _row(worker):
    return Worker.objects.get(pk=worker.pk)


@pytest.mark.django_db
class TestReportStored:

    def test_registration_stores_the_report_and_returns_it(self, make_worker_client):
        worker, client, _token = make_worker_client('cards-reg')
        resp = _post(client, 'cards-reg', os='Windows', cards=report())
        assert _row(worker).card_status == stored()
        assert resp.data['card_status'] == stored()
        assert resp.data['dismissed_card_alerts'] == []
        assert resp.data['card_alert_dismissals'] == []

    def test_heartbeat_updates_only_when_present(self, make_worker_client):
        worker, client, _token = make_worker_client('cards-hb')
        _post(client, 'cards-hb', cards=report())
        _post(client, 'cards-hb')
        assert _row(worker).card_status == stored()
        _post(client, 'cards-hb', cards=report(method='cpu', alerts=[]))
        assert _row(worker).card_status == stored(method='cpu', alerts=[])

    @pytest.mark.parametrize('garbage', ['yes', [], report(schema=2)])
    def test_unusable_report_keeps_the_stored_value(self, make_worker_client, garbage):
        worker, client, _token = make_worker_client('cards-bad')
        _post(client, 'cards-bad', cards=report())
        _post(client, 'cards-bad', cards=garbage)
        _post(client, 'cards-bad', os='Linux', cards=garbage)
        assert _row(worker).card_status == stored()

    def test_card_status_is_null_when_never_reported(self, make_worker_client, admin_client):
        worker, client, _token = make_worker_client('cards-null')
        resp = _post(client, 'cards-null')
        assert resp.data['card_status'] is None
        row = next(w for w in admin_client.get(HB).data if w['hostname'] == 'cards-null')
        assert row['card_status'] is None
        assert row['card_alert_dismissals'] == []

    def test_admin_listing_shows_the_report(self, make_worker_client, admin_client):
        worker, _client, _token = make_worker_client('cards-list')
        Worker.objects.filter(pk=worker.pk).update(card_status=stored(), card_alert_dismissals=[ALERT_ID])
        row = next(w for w in admin_client.get(HB).data if w['hostname'] == 'cards-list')
        assert row['card_status'] == stored()
        assert row['card_alert_dismissals'] == [ALERT_ID]

    def test_handler_save_never_writes_the_dismissals(self, make_worker_client, mocker):
        """A dismissal stored between the bind and the handler's save survives the save (D24)."""
        worker, client, _token = make_worker_client('cards-save')
        Worker.objects.filter(pk=worker.pk).update(card_status=stored())
        original = heartbeat_mod.apply_card_report

        def apply_then_dismiss(target, data):
            Worker.objects.filter(pk=worker.pk).update(card_alert_dismissals=[ALERT_ID])
            return original(target, data)
        mocker.patch.object(heartbeat_mod, 'apply_card_report', side_effect=apply_then_dismiss)
        resp = _post(client, 'cards-save', cards=report())
        assert _row(worker).card_alert_dismissals == [ALERT_ID]
        assert resp.data['dismissed_card_alerts'] == [ALERT_ID]


@pytest.mark.django_db
class TestDismissalRoundTrip:

    def _reported(self, make_worker_client, hostname):
        worker, client, _token = make_worker_client(hostname)
        _post(client, hostname, cards=report(alerts=TWO_ALERTS))
        return worker, client

    def test_dismissal_reaches_the_worker_then_is_pruned(self, make_worker_client, admin_client):
        worker, client = self._reported(make_worker_client, 'trip')
        admin_client.post(f'{HB}{worker.pk}/dismiss-card-alert/', {'alert_id': ALERT_ID}, format='json')
        resp = _post(client, 'trip', cards=report(alerts=TWO_ALERTS))
        assert resp.data['dismissed_card_alerts'] == [ALERT_ID]
        assert resp.data['card_alert_dismissals'] == [ALERT_ID]
        # The worker applied it and no longer reports the alert.
        resp = _post(client, 'trip', cards=report(alerts=[TWO_ALERTS[1]]))
        assert resp.data['dismissed_card_alerts'] == []
        assert _row(worker).card_alert_dismissals == []

    def test_dismissal_made_between_save_and_sync_is_kept(self, make_worker_client, mocker):
        worker, client = self._reported(make_worker_client, 'between')
        real_sync = heartbeat_card_sync.sync

        def dismiss_then_sync(target, data):
            Worker.objects.filter(pk=worker.pk).update(card_alert_dismissals=[ALERT_ID_2])
            return real_sync(target, data)
        mocker.patch.object(heartbeat_card_sync, 'sync', side_effect=dismiss_then_sync)
        resp = _post(client, 'between', cards=report(alerts=TWO_ALERTS))
        assert resp.data['dismissed_card_alerts'] == [ALERT_ID_2]
        assert _row(worker).card_alert_dismissals == [ALERT_ID_2]

    def test_dismissal_made_between_the_unlocked_read_and_the_lock_is_kept(self, make_worker_client, mocker):
        worker, client = self._reported(make_worker_client, 'relock')
        stale = 'f' * 32
        Worker.objects.filter(pk=worker.pk).update(card_alert_dismissals=[stale])
        real_prune = heartbeat_card_sync._prune_locked

        def dismiss_then_prune(pk):
            Worker.objects.filter(pk=pk).update(card_alert_dismissals=[stale, ALERT_ID])
            return real_prune(pk)
        mocker.patch.object(heartbeat_card_sync, '_prune_locked', side_effect=dismiss_then_prune)
        resp = _post(client, 'relock', cards=report(alerts=TWO_ALERTS))
        assert resp.data['dismissed_card_alerts'] == [ALERT_ID]
        assert _row(worker).card_alert_dismissals == [ALERT_ID]

    def test_no_lock_and_no_write_when_nothing_changes(self, make_worker_client, mocker):
        worker, client = self._reported(make_worker_client, 'quiet')
        Worker.objects.filter(pk=worker.pk).update(card_alert_dismissals=[ALERT_ID])
        prune = mocker.spy(heartbeat_card_sync, '_prune_locked')
        with CaptureQueriesContext(connection) as ctx:
            resp = _post(client, 'quiet', cards=report(alerts=TWO_ALERTS))
        assert prune.call_count == 0
        assert not [q for q in ctx.captured_queries
                    if 'card_alert_dismissals' in q['sql'] and q['sql'].startswith('UPDATE')]
        assert resp.data['dismissed_card_alerts'] == [ALERT_ID]

    def test_database_error_keeps_the_serializer_values(self, make_worker_client, mocker):
        worker, client = self._reported(make_worker_client, 'dberr')
        Worker.objects.filter(pk=worker.pk).update(card_alert_dismissals=[ALERT_ID])
        mocker.patch.object(heartbeat_card_sync, 'pruned_dismissals', side_effect=DatabaseError('locked'))
        warn = mocker.patch.object(heartbeat_card_sync.logger, 'warning')
        resp = _post(client, 'dberr', cards=report(alerts=TWO_ALERTS))
        assert warn.call_count == 1
        assert resp.data['dismissed_card_alerts'] == [ALERT_ID]
        assert resp.data['card_alert_dismissals'] == [ALERT_ID]
