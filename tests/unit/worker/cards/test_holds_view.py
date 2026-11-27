# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Render per card FR-PC-MGR-3, -4: each card's ``job_id`` and ``renderer``.

In the heartbeat's ``cards`` report (``cards.payload.build``) and in the
settings page's ``GET /api/cards/`` (``cards_view.view``), both built from
one ``WorkerCapacity.holds_snapshot()`` (``cards.holds_view``).

The capacity is a real ``WorkerCapacity`` with three split cards whose keys
are the inventory's cards' (``pci:0000:01:00.0``, ``02``, ``03``); holds are
taken with the real ``reserve_for_job`` (Blender) and
``reserve_unreal_card`` (Unreal). The saved section and the probe are fakes.
"""
import pytest

from sethlans_worker_agent import job_processor
from sethlans_worker_agent.capacity import WorkerCapacity
from sethlans_worker_agent.cards import holds_view, in_use, payload
from sethlans_worker_agent.cards.inventory import Card
from sethlans_worker_agent.web_ui import cards_view
from tests.unit.worker._card_choice import inv
from tests.unit.worker._card_pages import RUNTIME_GET, call, fake_probes, fake_saved
from tests.unit.worker.executors._fakes import key, profile

KEYS = (key(0), key(1), key(2))
FREE = {'job_id': None, 'renderer': None}


def _card(index):
    return Card(key=KEYS[index], name=f'GPU {index}', vendor_id=0x10DE, device_id=0x2704,
                blender=(f'CUDA_GPU {index}_0000:{index + 1:02x}:00', 'CUDA'))


INVENTORY = inv(_card(0), _card(1), _card(2))


@pytest.fixture
def cap():
    capacity = WorkerCapacity(profile(gpu_slot_count=3))
    job_processor._capacity = capacity  # the conftest resets it after each test
    return capacity


@pytest.fixture
def report_of(monkeypatch):
    """``report_of()``: the heartbeat ``cards`` report, cards by key."""
    fake_saved(monkeypatch, {'method': 'gpu', 'gpu_mode': 'split', 'revision': 3,
                             'cards': [in_use.record_of(_card(i)) for i in range(3)], 'known': list(KEYS)})
    blender_list = [{'id': card.blender[0], 'type': 'CUDA'} for card in INVENTORY.cards]
    monkeypatch.setattr(payload, '_probe_for_report', lambda: cards_view.Probe(INVENTORY, blender_list))

    def build():
        return {entry['key']: entry for entry in payload.build()['cards']}
    return build


def pair(entry):
    return {'job_id': entry['job_id'], 'renderer': entry['renderer']}


class TestHeartbeatReport:

    def test_held_blender_card(self, cap, report_of):
        assert cap.reserve_for_job(7, 'GPU').card_keys == (KEYS[0],)

        cards = report_of()

        assert pair(cards[KEYS[0]]) == {'job_id': 7, 'renderer': 'BLENDER'}
        assert pair(cards[KEYS[1]]) == FREE and pair(cards[KEYS[2]]) == FREE

    def test_held_unreal_card(self, cap, report_of):
        assert cap.reserve_unreal_card(9, KEYS[2]) is not None

        cards = report_of()

        assert pair(cards[KEYS[2]]) == {'job_id': 9, 'renderer': 'UNREAL'}
        assert pair(cards[KEYS[0]]) == FREE and pair(cards[KEYS[1]]) == FREE

    def test_free_cards_give_nulls(self, cap, report_of):
        cards = report_of()

        assert [pair(cards[k]) for k in KEYS] == [FREE] * 3
        assert all('job_id' in entry and 'renderer' in entry for entry in cards.values())

    def test_no_capacity_yet_gives_nulls(self, report_of):
        assert [pair(entry) for entry in report_of().values()] == [FREE] * 3

    def test_two_cards_held_by_two_jobs(self, cap, report_of):
        cap.reserve_for_job(7, 'GPU')
        cap.reserve_unreal_card(9, KEYS[2])

        cards = report_of()

        assert [pair(cards[k]) for k in KEYS] == [
            {'job_id': 7, 'renderer': 'BLENDER'}, FREE, {'job_id': 9, 'renderer': 'UNREAL'}]

    def test_released_card_is_free_again(self, cap, report_of):
        cap.reserve_unreal_card(9, KEYS[1])
        cap.release_slot(9)

        assert pair(report_of()[KEYS[1]]) == FREE

    def test_retained_hold_reports_its_job(self, cap, report_of):
        cap.reserve_unreal_card(9, KEYS[1])
        cap.retain_slots(9)

        assert pair(report_of()[KEYS[1]]) == {'job_id': 9, 'renderer': 'UNREAL'}

    def test_snapshot_taken_once_per_report(self, cap, report_of, mocker):
        cap.reserve_for_job(7, 'GPU')
        cap.reserve_unreal_card(9, KEYS[2])
        spy = mocker.spy(cap, 'holds_snapshot')

        report_of()

        assert spy.call_count == 1


class TestMatching:

    def test_held_key_matches_by_address_not_text(self):
        holds = holds_view.Holds({'pci:00000000:0a:00.0': (5, 'UNREAL')})  # an 8-digit domain

        assert holds.fields('pci:0000:0a:00.0') == {'job_id': 5, 'renderer': 'UNREAL'}

    def test_key_without_address_matches_by_text(self):
        holds = holds_view.Holds({'apple-gpu': (3, 'BLENDER')})

        assert holds.fields('apple-gpu') == {'job_id': 3, 'renderer': 'BLENDER'}
        assert holds.fields(key(0)) == FREE

    @pytest.mark.parametrize('job_id, renderer', [
        (0, 'UNREAL'), (-4, 'UNREAL'), (2 ** 31, 'BLENDER'), (True, 'BLENDER'), ('7', 'BLENDER'),
        (7, 'CYCLES'), (7, None), (None, 'UNREAL'), (None, None),
    ])
    def test_invalid_value_nulls_the_pair(self, job_id, renderer):
        assert holds_view.checked(job_id, renderer) == FREE

    def test_largest_job_id_is_kept(self):
        assert holds_view.checked(2 ** 31 - 1, 'UNREAL') == {'job_id': 2 ** 31 - 1, 'renderer': 'UNREAL'}


class TestSettingsEndpoint:

    @pytest.fixture(autouse=True)
    def _open(self, monkeypatch):
        fake_saved(monkeypatch, {'method': 'gpu', 'gpu_mode': 'split', 'revision': 3,
                                 'cards': [in_use.record_of(_card(0))], 'known': list(KEYS)})
        fake_probes(monkeypatch, INVENTORY)

    def test_get_gives_each_card_its_pair_from_one_snapshot(self, cap, mocker):
        cap.reserve_for_job(7, 'GPU')
        cap.reserve_unreal_card(9, KEYS[2])
        spy = mocker.spy(cap, 'holds_snapshot')

        status, answer = call(RUNTIME_GET)

        assert status == 200 and spy.call_count == 1
        assert [pair(entry) for entry in answer['cards']] == [
            {'job_id': 7, 'renderer': 'BLENDER'}, FREE, {'job_id': 9, 'renderer': 'UNREAL'}]

    def test_get_with_free_cards_gives_nulls(self, cap):
        status, answer = call(RUNTIME_GET)

        assert status == 200 and [pair(entry) for entry in answer['cards']] == [FREE] * 3
