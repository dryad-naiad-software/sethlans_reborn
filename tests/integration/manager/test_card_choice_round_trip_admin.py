# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Round trips of the worker's switch, a replaced request and a withdrawn one,
admin -> manager -> worker -> manager (worker card selection FR-CM-1,
FR-CM-5, FR-CM-10, FR-CM-11, FR-CM-13; AC-28, AC-29, AC-30).

Same plumbing as ``test_card_choice_round_trip`` (``_card_choice_trip_harness``):
the worker's real heartbeat, report, ``handle_response`` and ``config.json``
against the real heartbeat endpoint and card-choice action.
"""
import pytest

from sethlans_worker_agent.capacity import rebuild
from sethlans_worker_agent.cards import store_requests

from ._card_choice_trip_harness import BASE_REVISION, BOTH, NOT_ALLOWED_409, trip_worker, without
from ._card_contract_harness import KEY_OK, KEY_UNTICKED


@pytest.fixture
def trip(monkeypatch, tmp_path, make_worker_client):
    yield from trip_worker(monkeypatch, tmp_path, make_worker_client)


def _keys(compute):
    return [entry['key'] for entry in compute['cards']]


def _outcome(trip, pending):
    """The manager's form of the outcome in the last report, for ``pending`` (its issued entry)."""
    return {**trip.last_report()['request_outcome'], 'requested_by': pending['requested_by'],
            'requested_at': pending['requested_at']}


def _switch_off(trip):
    store_requests.set_allow_manager_changes(False, 'settings page')
    assert trip.machine.compute()['allow_manager_changes'] is False


@pytest.mark.django_db
class TestSwitchOff:

    def test_reported_switch_off_is_409_at_the_action(self, trip, admin_client):
        """AC-28: once the report says ``manager_changes_allowed: false`` the action refuses to store."""
        trip.register()
        assert trip.row().card_status['manager_changes_allowed'] is True
        _switch_off(trip)
        trip.beat()
        assert trip.last_report()['manager_changes_allowed'] is False
        assert trip.row().card_status['manager_changes_allowed'] is False

        assert trip.post(admin_client, expect=409) == {'detail': NOT_ALLOWED_409}

        assert trip.row().card_choice_request == {}
        assert trip.beat()['card_choice_request'] is None
        assert 'last_request' not in trip.machine.compute()

    def test_request_pending_when_the_switch_went_off_is_not_allowed(self, trip, admin_client):
        """AC-28: a request stored while allowed, delivered after the switch went off -> ``not_allowed``."""
        trip.register()
        pending = trip.post(admin_client)['card_choice_request']
        _switch_off(trip)
        before = trip.machine.compute()

        assert trip.beat()['card_choice_request'] == pending

        after = trip.machine.compute()
        assert without(after) == without(before)
        assert after['allow_manager_changes'] is False  # no request changes the switch
        assert (after['last_request']['id'], after['last_request']['code']) == (pending['id'], 'not_allowed')
        assert rebuild.state() == (0, False)

        answer = trip.beat()

        assert trip.last_report()['request_outcome']['code'] == 'not_allowed'
        assert answer['card_choice_request'] is None
        assert answer['card_choice_outcome'] == _outcome(trip, pending)
        assert answer['card_choice_outcome']['result'] == 'refused'
        assert trip.row().card_choice_request == {}


@pytest.mark.django_db
class TestReplaced:

    def test_late_outcome_of_a_delivered_old_request_updates_only_the_outcome(self, trip, admin_client):
        """AC-29: the old request was applied; its outcome is shown and the new request stays pending."""
        trip.register()
        old = trip.post(admin_client)['card_choice_request']
        assert trip.beat()['card_choice_request'] == old  # delivered and applied (revision 4)
        assert trip.machine.compute()['revision'] == BASE_REVISION + 1
        assert trip.row().card_status['revision'] == BASE_REVISION  # the report went out before the save

        sent = trip.post(admin_client, keys=[KEY_UNTICKED], gpu_mode='split')  # based on the manager's view: 3
        new = sent['card_choice_request']
        assert sent['replaced'] == old['id']

        answer = trip.beat()  # reports the old id's outcome

        assert trip.last_report()['request_outcome']['id'] == old['id']
        assert answer['card_choice_outcome'] == _outcome(trip, old)
        assert answer['card_choice_request'] == new
        row = trip.row()
        assert row.card_choice_request == new
        assert row.card_choice_outcome == _outcome(trip, old)
        # The new request was evaluated with this answer: revision 3 is stale on the worker now.
        compute = trip.machine.compute()
        assert (compute['last_request']['id'], compute['last_request']['code']) == (new['id'], 'stale_revision')
        assert _keys(compute) == BOTH
        assert rebuild.state() == (1, True)

        answer = trip.beat()

        assert answer['card_choice_request'] is None
        assert answer['card_choice_outcome'] == _outcome(trip, new)
        assert trip.row().card_choice_request == {}

    def test_request_replaced_before_delivery_never_reaches_the_worker(self, trip, admin_client):
        """AC-29: only the pending request is delivered; the replaced one is never evaluated."""
        trip.register()
        old = trip.post(admin_client)['card_choice_request']
        sent = trip.post(admin_client, keys=[KEY_UNTICKED], gpu_mode='split')
        assert sent['replaced'] == old['id']
        new = sent['card_choice_request']

        assert trip.beat()['card_choice_request'] == new

        compute = trip.machine.compute()
        assert _keys(compute) == [KEY_UNTICKED]
        assert compute['last_request']['id'] == new['id']
        assert old['id'] not in compute.get('recent_request_ids', [])
        answer = trip.beat()
        assert answer['card_choice_request'] is None
        assert answer['card_choice_outcome'] == _outcome(trip, new)
        assert answer['card_choice_outcome']['code'] == 'applied'


@pytest.mark.django_db
class TestWithdrawn:

    def test_withdrawn_request_is_not_delivered(self, trip, admin_client):
        """AC-30: DELETE 204, the next answer has ``card_choice_request: null``, a second DELETE is 409."""
        trip.register()
        trip.post(admin_client)
        before = trip.machine.compute()

        trip.delete(admin_client)

        answer = trip.beat()
        assert answer['card_choice_request'] is None
        assert trip.machine.compute() == before
        assert rebuild.state() == (0, False)
        assert trip.last_report()['request_outcome'] is None
        assert trip.delete(admin_client, expect=409).json() == {'detail': "No change is waiting for this worker."}

    def test_withdrawn_after_delivery_still_shows_the_outcome(self, trip, admin_client):
        """FR-CM-11: a worker that already received the request may apply it; its outcome is then shown."""
        trip.register()
        pending = trip.post(admin_client)['card_choice_request']
        trip.beat()  # delivered and applied
        assert _keys(trip.machine.compute()) == BOTH

        trip.delete(admin_client)
        answer = trip.beat()

        assert answer['card_choice_request'] is None
        assert answer['card_choice_outcome'] == _outcome(trip, pending)
        assert answer['card_choice_outcome']['code'] == 'applied'
        assert trip.row().card_choice_outcome == _outcome(trip, pending)
        assert KEY_OK in _keys(trip.machine.compute())
