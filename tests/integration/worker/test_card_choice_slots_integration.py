# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Saved card choice to slots, poll parameters and claims (card selection, chunk 2).

Each test saves a ``compute`` section, lets the real ``init_capacity`` build
the profile from it (``config_compute`` -> ``cards.in_use`` -> capacity
profile), then drives ``job_processor.poll_and_claim_job`` with a mocked
manager. Covered: FR-CL-2 (slots per compute method and GPU mode), FR-CL-3
(slots keyed by card key), and that an unticked card never gets a slot.
"""

import pytest

from sethlans_worker_agent import api_handler, job_processor, job_selection

from ._card_choice_harness import ID_A, ID_B, KEY_A, KEY_B, KEY_C, blender_job, install_card_choice


@pytest.fixture(autouse=True)
def _clean_claim_records():
    """Claimed jobs are never run here; forget them so ids can be reused by the next test."""
    job_selection.reset_for_tests()
    yield
    job_selection.reset_for_tests()
    with job_processor._active_jobs_lock:
        job_processor._unfinished_job_ids.clear()


@pytest.fixture()
def manager(mocker):
    """The manager's poll and claim endpoints, as ``(poll, claim)`` mocks."""
    poll = mocker.patch.object(api_handler, 'poll_for_available_jobs', return_value=[])
    claim = mocker.patch.object(api_handler, 'claim_job', return_value=True)
    return poll, claim


def _prefs_sent(poll):
    params = poll.call_args.args[0]
    return set(params['device_prefs'].split(','))


def _poll_once(poll, jobs):
    poll.reset_mock()
    poll.return_value = list(jobs)
    return job_processor.poll_and_claim_job(worker_id=1)


class TestCardChoiceSlotsIntegration:
    """FR-CL-2: slots and poll parameters follow the saved compute method."""

    def test_cpu_method_has_cpu_slot_only_and_never_claims_gpu(self, mocker, manager):
        poll, claim = manager
        capacity, probe = install_card_choice(mocker, method='cpu', ticked=(KEY_A, KEY_C))

        profile = capacity.profile
        assert (profile.gpu_slot_count, profile.cpu_slot_count) == (0, 1)
        assert profile.cards == () and profile.problem == ''
        probe.assert_not_called()  # method cpu: no card is probed (B6)

        assert _poll_once(poll, [blender_job(1, 'GPU')]) is None
        assert _prefs_sent(poll) == {'CPU', 'ANY'}
        claim.assert_not_called()
        assert capacity.gpu_assignments_snapshot() == {}

        claimed = _poll_once(poll, [blender_job(2, 'ANY')])
        assert claimed['_reservation'].device_used == 'CPU'
        assert claimed['_reservation'].card_keys == ()

    def test_gpu_method_split_has_one_slot_per_ticked_card_and_no_cpu(self, mocker, manager):
        poll, claim = manager
        capacity, _probe = install_card_choice(mocker, method='gpu', ticked=(KEY_A, KEY_C))

        profile = capacity.profile
        assert (profile.gpu_slot_count, profile.cpu_slot_count) == (2, 0)
        assert profile.card_keys == (KEY_A, KEY_C)

        assert _poll_once(poll, [blender_job(1, 'CPU')]) is None
        assert _prefs_sent(poll) == {'GPU', 'ANY'}
        claim.assert_not_called()

        first = _poll_once(poll, [blender_job(2, 'GPU')])
        second = _poll_once(poll, [blender_job(3, 'ANY')])
        assert first['_reservation'].card_keys == (KEY_A,)
        assert second['_reservation'].card_keys == (KEY_C,)
        assert second['_reservation'].device_used == 'GPU'

        # Both cards held, no CPU slot: the worker does not poll at all.
        assert _poll_once(poll, [blender_job(4, 'GPU')]) is None
        poll.assert_not_called()
        assert capacity.is_full()

    def test_cpu_gpu_method_has_both_kinds_of_slot(self, mocker, manager):
        poll, _claim = manager
        capacity, _probe = install_card_choice(mocker, method='cpu_gpu', ticked=(KEY_A, KEY_C))

        assert (capacity.profile.gpu_slot_count, capacity.profile.cpu_slot_count) == (2, 1)
        assert _poll_once(poll, [blender_job(1, 'GPU')])['_reservation'].card_keys == (KEY_A,)
        assert _prefs_sent(poll) == {'GPU', 'CPU', 'ANY'}
        assert _poll_once(poll, [blender_job(2, 'CPU')])['_reservation'].device_used == 'CPU'
        assert _poll_once(poll, [blender_job(3, 'GPU')])['_reservation'].card_keys == (KEY_C,)
        assert _prefs_sent(poll) == {'GPU', 'ANY'}  # asked before job 3: CPU slot already held
        assert capacity.is_full()
        assert capacity.gpu_assignments_snapshot() == {KEY_A: 1, KEY_C: 3}

    def test_combined_mode_has_one_slot_holding_every_ticked_card(self, mocker, manager):
        poll, _claim = manager
        capacity, _probe = install_card_choice(mocker, method='gpu', gpu_mode='combined', ticked=(KEY_A, KEY_C))

        assert capacity.profile.gpu_slot_count == 1
        reservation = _poll_once(poll, [blender_job(1, 'GPU')])['_reservation']
        assert reservation.card_keys == (KEY_A, KEY_C)
        assert capacity.gpu_assignments_snapshot() == {KEY_A: 1, KEY_C: 1}
        assert capacity.is_full()


class TestUntickedCardsIntegration:
    """Only ticked cards are in use; an unticked card is switched off, never reserved."""

    @pytest.mark.parametrize('gpu_mode', ['split', 'combined'])
    def test_unticked_card_gets_no_slot(self, mocker, manager, gpu_mode):
        poll, _claim = manager
        capacity, _probe = install_card_choice(mocker, method='cpu_gpu', gpu_mode=gpu_mode, ticked=(KEY_A, KEY_C))

        assert KEY_B not in capacity.profile.card_keys
        assert capacity.profile.ignored == (ID_B,)
        job_id = 1
        while _poll_once(poll, [blender_job(job_id, 'GPU')]) is not None:
            job_id += 1
        held = capacity.gpu_assignments_snapshot()
        assert set(held) == {KEY_A, KEY_C}
        assert KEY_B not in held

    def test_reservation_copies_only_cards_in_use(self, mocker, manager):
        poll, _claim = manager
        capacity, _probe = install_card_choice(mocker, method='gpu', ticked=(KEY_C,))

        reservation = _poll_once(poll, [blender_job(1, 'GPU')])['_reservation']
        assert [card.key for card in reservation.cards] == [KEY_C]
        assert [card.key for card in reservation.job_cards] == [KEY_C]
        assert set(reservation.ignored) == {ID_A, ID_B}
        assert capacity.profile.gpu_slot_count == 1

    def test_ticked_card_missing_from_probe_gets_no_slot(self, mocker, manager):
        poll, _claim = manager
        capacity, _probe = install_card_choice(mocker, method='cpu_gpu', ticked=(KEY_A, KEY_C),
                                               present=(KEY_B, KEY_C))

        assert capacity.profile.card_keys == (KEY_C,)
        assert _poll_once(poll, [blender_job(1, 'GPU')])['_reservation'].card_keys == (KEY_C,)
        assert _poll_once(poll, [blender_job(2, 'GPU')]) is None
        assert set(capacity.gpu_assignments_snapshot()) == {KEY_C}
