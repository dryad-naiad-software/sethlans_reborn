# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the pending card change in heartbeat answers and the
worker's outcome (worker card selection FR-CM-12, FR-CM-13, D24, AC-23
manager part, AC-29, AC-30, AC-33, AC-35).
"""

import json

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from sethlans_worker_agent.cards import remote
from tests.unit.manager.card_report_helpers import ALERT_ID
from workers.models import Worker
from workers.views import heartbeat_card_outcome, heartbeat_card_sync

from .card_choice_helpers import HB, body, outcome, reported, row, two_cards, url

UNKNOWN = 'f' * 32


def _beat(client, hostname, request_outcome=None, **extra):
    report = {**two_cards(), 'request_outcome': request_outcome}
    resp = client.post(HB, {'hostname': hostname, 'cards': report, **extra}, format='json')
    assert resp.status_code == 200, resp.data
    return resp.data


def _send(admin_client, worker, **overrides):
    resp = admin_client.post(url(worker.pk), body(**overrides), format='json')
    assert resp.status_code == 202, resp.data
    return resp.data['card_choice_request']


@pytest.fixture(autouse=True)
def _fresh_warnings(monkeypatch):
    monkeypatch.setattr(heartbeat_card_outcome, '_warned_unknown', {})


@pytest.mark.django_db
class TestDelivery:

    def test_applied_round_trip(self, admin_client, admin_user, make_worker_client, mocker):
        """AC-23 (manager part): delivered until the outcome arrives, then cleared and shown."""
        worker, client = reported(make_worker_client, 'trip')
        assert _beat(client, 'trip')['card_choice_request'] is None
        pending = _send(admin_client, worker)
        assert _beat(client, 'trip')['card_choice_request'] == pending
        assert _beat(client, 'trip', os='Windows')['card_choice_request'] == pending  # registration too
        info = mocker.patch.object(heartbeat_card_outcome.logger, 'info')
        answer = _beat(client, 'trip', outcome(pending['id']))
        assert answer['card_choice_request'] is None
        expected = {**outcome(pending['id']), 'requested_by': f'user id={admin_user.pk}',
                    'requested_at': pending['requested_at']}
        assert answer['card_choice_outcome'] == expected
        stored = row(worker)
        assert (stored.card_choice_request, stored.card_choice_outcome) == ({}, expected)
        assert info.call_args.args[0] % info.call_args.args[1:] == (
            f"Worker trip applied graphics card change {pending['id']} (applied).")

    def test_refused_outcome_is_stored_with_its_reason(self, admin_client, make_worker_client):
        worker, client = reported(make_worker_client, 'refused')
        pending = _send(admin_client, worker)
        reason = "the choice was changed on the worker (revision 4) after the manager's view (revision 3)"
        answer = _beat(client, 'refused', outcome(pending['id'], result='refused', code='stale_revision',
                                                  reason=reason, revision=4))
        assert answer['card_choice_request'] is None
        assert answer['card_choice_outcome']['code'] == 'stale_revision'
        assert answer['card_choice_outcome']['reason'] == reason

    def test_late_outcome_for_a_replaced_request(self, admin_client, make_worker_client):
        """AC-29: accepted into the outcome; the new request stays pending."""
        worker, client = reported(make_worker_client, 'late')
        old = _send(admin_client, worker)
        new = _send(admin_client, worker, method='cpu_gpu')
        answer = _beat(client, 'late', outcome(old['id']))
        assert answer['card_choice_request'] == new
        assert answer['card_choice_outcome']['id'] == old['id']
        answer = _beat(client, 'late', outcome(new['id'], revision=5))
        assert answer['card_choice_request'] is None
        assert answer['card_choice_outcome']['id'] == new['id']
        # The worker never reports the older one again; if it did, nothing would change.
        assert _beat(client, 'late', outcome(old['id']))['card_choice_outcome']['id'] == new['id']

    def test_withdrawn_request_is_no_longer_delivered(self, admin_client, make_worker_client):
        """AC-30, FR-CM-11: its outcome is still shown when the worker had already received it."""
        worker, client = reported(make_worker_client, 'withdrawn')
        pending = _send(admin_client, worker)
        assert admin_client.delete(url(worker.pk)).status_code == 204
        assert _beat(client, 'withdrawn')['card_choice_request'] is None
        answer = _beat(client, 'withdrawn', outcome(pending['id']))
        assert answer['card_choice_outcome']['id'] == pending['id']

    def test_issued_ids_are_never_exposed(self, admin_client, make_worker_client):
        """AC-35: neither the worker list nor any heartbeat answer carries the issued list or a username."""
        worker, client = reported(make_worker_client, 'hidden')
        pending = _send(admin_client, worker)
        answers = [_beat(client, 'hidden'), _beat(client, 'hidden', os='Linux'),
                   _beat(client, 'hidden', outcome(pending['id']))]
        listing = [w for w in admin_client.get(HB).data if w['hostname'] == 'hidden']
        for data in answers + listing:
            assert 'card_choice_issued' not in data
            text = json.dumps(data)
            assert 'admin' not in text and 'issued' not in text
        assert listing[0]['card_choice_outcome']['requested_by'].startswith('user id=')


@pytest.mark.django_db
class TestOutcomeValidation:

    def test_unknown_id_is_dropped_with_one_warning(self, admin_client, make_worker_client, mocker):
        """AC-33."""
        worker, client = reported(make_worker_client, 'unknown')
        pending = _send(admin_client, worker)
        warn = mocker.patch.object(heartbeat_card_outcome.logger, 'warning')
        for _ in range(3):
            answer = _beat(client, 'unknown', outcome(UNKNOWN))
        assert answer['card_choice_request'] == pending
        assert answer['card_choice_outcome'] is None
        assert warn.call_count == 1
        assert 'unknown' in warn.call_args.args[0] % warn.call_args.args[1:]

    def test_id_issued_to_another_worker_is_dropped(self, admin_client, make_worker_client):
        worker, client = reported(make_worker_client, 'mine')
        other, _other_client = reported(make_worker_client, 'other')
        theirs = _send(admin_client, other)
        _send(admin_client, worker)
        assert _beat(client, 'mine', outcome(theirs['id']))['card_choice_outcome'] is None
        assert row(other).card_choice_request == theirs

    @pytest.mark.parametrize('overrides', [
        {'result': 'refused'}, {'code': 'later'}, {'revision': True}, {'finished_at': '2026-11-19 12:00'},
        {'reason': 'x' * 201},
    ])
    def test_field_outside_the_rules_drops_the_outcome(self, admin_client, make_worker_client, overrides):
        worker, client = reported(make_worker_client, 'bad-outcome')
        pending = _send(admin_client, worker)
        answer = _beat(client, 'bad-outcome', outcome(pending['id'], **overrides))
        assert answer['card_choice_request'] == pending
        assert row(worker).card_choice_outcome == {}


@pytest.mark.django_db
class TestLocking:

    def test_repeated_outcome_takes_no_lock_and_writes_nothing(self, admin_client, make_worker_client, mocker):
        worker, client = reported(make_worker_client, 'repeat')
        pending = _send(admin_client, worker)
        _beat(client, 'repeat', outcome(pending['id']))
        stored = row(worker).card_choice_outcome
        locked = mocker.spy(heartbeat_card_sync, '_prune_locked')
        info = mocker.patch.object(heartbeat_card_outcome.logger, 'info')
        with CaptureQueriesContext(connection) as ctx:
            answer = _beat(client, 'repeat', outcome(pending['id']))
        assert locked.call_count == 0 and info.call_count == 0
        assert not [q for q in ctx.captured_queries if q['sql'].startswith('UPDATE') and 'card_choice' in q['sql']]
        assert answer['card_choice_outcome'] == stored
        assert row(worker).card_choice_outcome == stored

    def test_pending_request_without_outcome_takes_no_lock(self, admin_client, make_worker_client, mocker):
        worker, client = reported(make_worker_client, 'waiting')
        pending = _send(admin_client, worker)
        locked = mocker.spy(heartbeat_card_sync, '_prune_locked')
        assert _beat(client, 'waiting')['card_choice_request'] == pending
        assert locked.call_count == 0

    def test_request_replaced_between_the_unlocked_read_and_the_lock(self, admin_client, make_worker_client, mocker):
        """The outcome for the old pending id must not clear a request stored meanwhile."""
        worker, client = reported(make_worker_client, 'race')
        old = _send(admin_client, worker)
        real = heartbeat_card_sync._prune_locked
        replaced = {}

        def replace_then_lock(pk, *args):
            replaced.update(_send(admin_client, worker, method='cpu_gpu'))
            return real(pk, *args)
        mocker.patch.object(heartbeat_card_sync, '_prune_locked', side_effect=replace_then_lock)
        answer = _beat(client, 'race', outcome(old['id']))
        assert answer['card_choice_request'] == replaced
        assert answer['card_choice_outcome']['id'] == old['id']

    def test_outcome_and_dismissal_prune_in_one_write(self, admin_client, make_worker_client):
        worker, client = reported(make_worker_client, 'both')
        Worker.objects.filter(pk=worker.pk).update(card_alert_dismissals=[ALERT_ID])
        pending = _send(admin_client, worker)
        answer = _beat(client, 'both', outcome(pending['id']))
        assert answer['dismissed_card_alerts'] == []
        assert answer['card_choice_request'] is None
        stored = row(worker)
        assert (stored.card_alert_dismissals, stored.card_choice_request) == ([], {})


@pytest.mark.django_db
class TestWorkerContract:
    """The manager's request passes the worker's own shape check, and the worker's outcome the manager's."""

    @pytest.mark.parametrize('overrides', [{}, {'method': 'cpu', 'cards': [], 'shown': []}])
    def test_request_passes_the_worker_shape_check(self, admin_client, make_worker_client, overrides):
        worker, client = reported(make_worker_client, 'contract')
        _send(admin_client, worker, **overrides)
        delivered = json.loads(json.dumps(_beat(client, 'contract')['card_choice_request']))
        assert remote.bad_field(delivered) is None

    @pytest.mark.parametrize('code, reason', [
        ('applied', ''), ('identity_mismatch', 'pci:0000:01:00.0 is not on this machine'),
    ])
    def test_worker_outcome_record_is_accepted(self, admin_client, make_worker_client, code, reason):
        worker, client = reported(make_worker_client, 'contract-out')
        pending = _send(admin_client, worker)
        record = remote._record(pending['id'], pending, code, reason, 4)
        reported_form = {name: record[name] for name in ('id', 'result', 'code', 'reason', 'revision', 'finished_at')}
        answer = _beat(client, 'contract-out', reported_form)
        assert answer['card_choice_request'] is None
        assert answer['card_choice_outcome']['code'] == code
        assert answer['card_choice_outcome']['requested_by'] == record['requested_by']
