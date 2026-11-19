# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Round trip of a graphics card change, admin -> manager -> worker -> manager
(worker card selection FR-CM-3..8, FR-CM-10, FR-CM-12, FR-CM-13; AC-23,
AC-24, AC-25, AC-31).

The admin's real action stores the request; the worker's real heartbeat
(``system_monitor.send_heartbeat``, HTTP routed to the Django test client)
receives it and its real ``cards.remote.handle_response`` applies or refuses
it against a real ``config.json``; the next heartbeat's
``cards.request_outcome`` is processed by the real heartbeat endpoint.
"""
import pytest

from sethlans_worker_agent.capacity import rebuild
from sethlans_worker_agent.cards import choice, remote

from ._card_choice_trip_harness import (
    BASE_REVISION, BOTH, DEVICE, OTHER_DEVICE, TRIP_BLENDER, TRIP_OS, fresh_inventory, trip_worker, without,
)
from ._card_contract_harness import KEY_OK, KEY_UNTICKED, NVIDIA, RTX, os_card, stored_form


@pytest.fixture
def trip(monkeypatch, tmp_path, make_worker_client):
    yield from trip_worker(monkeypatch, tmp_path, make_worker_client)


def _keys(compute):
    return [entry['key'] for entry in compute['cards']]


def _manager_outcome(report, pending):
    """What the manager must store for ``report``'s outcome: plus requested_by/_at from the issued entry."""
    return {**report['request_outcome'], 'requested_by': pending['requested_by'],
            'requested_at': pending['requested_at']}


def _refused(trip, pending, code):
    """The worker refused ``pending`` with ``code``; the next heartbeat makes it the manager's outcome."""
    last = trip.machine.compute()['last_request']
    assert (last['id'], last['result'], last['code']) == (pending['id'], 'refused', code)
    answer = trip.beat()
    report = trip.last_report()
    assert report['request_outcome']['id'] == pending['id']
    assert answer['card_choice_request'] is None
    assert answer['card_choice_outcome'] == _manager_outcome(report, pending)
    row = trip.row()
    assert row.card_choice_request == {}
    assert row.card_choice_outcome == _manager_outcome(report, pending)
    return last


@pytest.mark.django_db
class TestAppliedRoundTrip:

    def test_applied_change_is_saved_once_and_reported_back(self, trip, admin_client, admin_user):
        """AC-23: POST 202 -> delivered -> saved (revision + 1, one rebuild) -> outcome clears the request."""
        assert trip.register()['card_choice_request'] is None
        assert trip.row().card_status['revision'] == BASE_REVISION
        sent = trip.post(admin_client)
        pending = sent['card_choice_request']
        assert sent['replaced'] is None
        assert trip.row().card_choice_request == pending
        before = trip.machine.compute()

        # A registration answer carries the request but is never handled (FR-CM-3, FR-CM-12).
        assert trip.register()['card_choice_request'] == pending
        assert trip.machine.compute() == before

        answer = trip.beat()

        assert answer['card_choice_request'] == pending
        assert trip.last_report()['request_outcome'] is None  # built before the answer was handled
        after = trip.machine.compute()
        assert after['revision'] == BASE_REVISION + 1
        assert (_keys(after), after['gpu_mode'], after['method']) == (BOTH, 'combined', 'gpu')
        last = after['last_request']
        assert (last['id'], last['result'], last['code'], last['reason']) == (pending['id'], 'applied', 'applied', '')
        assert last['revision'] == BASE_REVISION + 1
        assert (last['requested_by'], last['requested_at']) == (f'user id={admin_user.pk}', pending['requested_at'])
        assert rebuild.state() == (1, True)  # one rebuild requested

        answer = trip.beat()

        report = trip.last_report()
        assert report['request_outcome'] == {name: last[name] for name in (
            'id', 'result', 'code', 'reason', 'revision', 'finished_at')}
        assert report['revision'] == BASE_REVISION + 1
        assert sorted(entry['key'] for entry in report['cards'] if entry['ticked']) == BOTH
        assert answer['card_choice_request'] is None
        assert answer['card_choice_outcome'] == _manager_outcome(report, pending)
        row = trip.row()
        assert row.card_choice_request == {}
        assert row.card_choice_outcome == _manager_outcome(report, pending)
        assert row.card_status == stored_form(report)

        # Later heartbeats repeat the same outcome: nothing changes on either side.
        trip.beat()
        assert trip.machine.compute() == after
        assert trip.row().card_choice_outcome == _manager_outcome(report, pending)
        assert rebuild.state() == (1, True)

    def test_repeat_delivery_is_saved_once(self, trip, admin_client):
        """AC-31: the same request in two answers is evaluated once; the same outcome is reported both times."""
        trip.register()
        pending = trip.post(admin_client)['card_choice_request']
        first = trip.beat()
        after = trip.machine.compute()
        assert after['revision'] == BASE_REVISION + 1
        assert rebuild.state() == (1, True)

        remote.handle_response(first)  # the same answer again (a lost acknowledgement, a retried request)
        remote.handle_response({**first, 'card_choice_request': dict(pending)})

        assert trip.machine.compute() == after
        assert rebuild.state() == (1, True)
        trip.beat()
        trip.beat()
        assert trip.sent[-1]['cards']['request_outcome'] == trip.sent[-2]['cards']['request_outcome']
        assert trip.sent[-1]['cards']['request_outcome']['id'] == pending['id']
        assert trip.machine.compute() == after
        assert rebuild.state() == (1, True)


@pytest.mark.django_db
class TestRefusedRoundTrip:

    def test_identity_mismatch_changes_only_the_outcome(self, trip, admin_client):
        """AC-24: the reported card is another model in the worker's fresh probe -> ``identity_mismatch``."""
        trip.register()
        assert trip.reported_ids()[KEY_UNTICKED] == {'vendor_id': NVIDIA, 'device_id': DEVICE}
        pending = trip.post(admin_client)['card_choice_request']
        trip.machine.install(os_cards=(TRIP_OS[0], os_card(KEY_UNTICKED, RTX, NVIDIA, OTHER_DEVICE)),
                             blender_cards=TRIP_BLENDER)
        before = trip.machine.compute()

        assert trip.beat()['card_choice_request'] == pending

        after = trip.machine.compute()
        assert without(after) == without(before)
        assert rebuild.state() == (0, False)
        last = _refused(trip, pending, 'identity_mismatch')
        assert last['reason'] == f'{KEY_UNTICKED} is now {RTX} (10de:{OTHER_DEVICE:04x})'
        assert last['revision'] == BASE_REVISION
        assert trip.machine.compute()['revision'] == BASE_REVISION

    def test_worker_page_save_before_delivery_is_stale_revision(self, trip, admin_client):
        """AC-25: a save on the worker after the admin's view -> ``stale_revision``; the page's choice stays."""
        trip.register()
        pending = trip.post(admin_client)['card_choice_request']
        assert pending['base_revision'] == BASE_REVISION

        # The worker's settings page saves through the same function (FR-CM-6) before the next heartbeat.
        saved = choice.save_choice('gpu', 'split', [KEY_UNTICKED], BOTH, BASE_REVISION, fresh_inventory())
        assert saved == BASE_REVISION + 1
        page = trip.machine.compute()
        assert rebuild.state() == (1, True)

        assert trip.beat()['card_choice_request'] == pending  # the manager re-checks nothing at delivery

        after = trip.machine.compute()
        assert without(after) == without(page)
        assert _keys(after) == [KEY_UNTICKED]
        assert rebuild.state() == (1, True)  # only the page's save requested one
        last = _refused(trip, pending, 'stale_revision')
        assert last['revision'] == BASE_REVISION + 1
        assert f'(revision {BASE_REVISION + 1})' in last['reason']
        assert f"manager's view (revision {BASE_REVISION})" in last['reason']
        assert trip.row().card_status['revision'] == BASE_REVISION + 1

    def test_stale_base_revision_is_409_at_the_action(self, trip, admin_client):
        """AC-25 (manager part): a base revision other than the reported one is refused before storing."""
        trip.register()
        resp = trip.post(admin_client, expect=409, base_revision=BASE_REVISION - 1)
        assert resp == {'detail': "The worker's card choice changed since this page was loaded. Reload and try again."}
        assert trip.row().card_choice_request == {}
        assert trip.beat()['card_choice_request'] is None
        assert 'last_request' not in trip.machine.compute()
        assert _keys(trip.machine.compute()) == [KEY_OK]
