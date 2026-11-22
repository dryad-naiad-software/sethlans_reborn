# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``renderers.unreal_machine`` live-reading helpers (Unreal per-machine memory
test D14, FR-MT-T-1; gpu-memory-reader FR-GH-4).

``reading_measures_card``, ``measures_card`` and ``eligible_kinds`` read only
``worker.gpu_memory`` and ``worker.card_status``, so a plain object stands in
for the ``Worker`` row (no database).  ``machine_poll_q`` for a worker
without kinds builds only a ``Q`` (no query runs).

- A live card reading is ``capabilities.card is True`` exactly.
- Without one, a worker has no kinds whatever its cards say.
- With one, the kinds are ``worker_kinds``.
- A worker without kinds is offered only ``shots`` jobs.
"""
from types import SimpleNamespace

import pytest
from django.db.models import Q

from workers.constants import UnrealKind
from workers.renderers import unreal_machine as um

LIVE = {'capabilities': {'card': True, 'process': True}, 'cards': []}


def _card(device=0x744C, revision=0xCC, ticked=True, state='ok'):
    return {'key': f'pci:{device:04x}', 'name': 'Card', 'vendor_id': 0x1002, 'device_id': device,
            'revision_id': revision, 'ticked': ticked, 'state': state}


def _worker(gpu_memory=LIVE, *cards):
    return SimpleNamespace(gpu_memory=gpu_memory, card_status={'schema': 1, 'cards': list(cards)})


class TestReadingMeasuresCard:

    def test_live_reading(self):
        assert um.reading_measures_card(LIVE) is True

    def test_card_only_capability(self):
        assert um.reading_measures_card({'capabilities': {'card': True}}) is True

    @pytest.mark.parametrize('card', [False, None, 1, 'true', 'True', [True], {'card': True}],
                             ids=['false', 'none', 'one', 'str-true', 'str-True', 'list', 'dict'])
    def test_not_exactly_true(self, card):
        assert um.reading_measures_card({'capabilities': {'card': card}}) is False

    @pytest.mark.parametrize('reading', [
        None, {}, [], 'live', 0, True, {'capabilities': None}, {'capabilities': []},
        {'capabilities': 'card'}, {'capabilities': {}}, {'capabilities': {'process': True}},
        {'card': True}, [('capabilities', {'card': True})],
    ], ids=['none', 'empty', 'list', 'str', 'zero', 'true', 'caps-none', 'caps-list', 'caps-str',
            'caps-empty', 'process-only', 'top-level-card', 'pairs'])
    def test_malformed_reading(self, reading):
        assert um.reading_measures_card(reading) is False

    def test_reading_is_not_changed(self):
        reading = {'capabilities': {'card': True}}
        um.reading_measures_card(reading)
        assert reading == {'capabilities': {'card': True}}


class TestMeasuresCard:

    def test_reads_the_worker_gpu_memory(self):
        assert um.measures_card(_worker(LIVE)) is True
        assert um.measures_card(_worker({'capabilities': {'card': False}})) is False
        assert um.measures_card(_worker(None)) is False


class TestEligibleKinds:

    def test_live_reading_gives_worker_kinds(self):
        worker = _worker(LIVE, _card(0x744C), _card(0x7448))
        assert um.eligible_kinds(worker) == um.worker_kinds(worker) == ('1002:7448:00cc', '1002:744c:00cc')

    @pytest.mark.parametrize('reading', [None, {}, {'capabilities': {'card': False}}, {'capabilities': {'card': 1}}],
                             ids=['none', 'empty', 'card-false', 'card-one'])
    def test_no_live_reading_gives_no_kinds(self, reading):
        assert um.eligible_kinds(_worker(reading, _card())) == ()

    def test_no_live_reading_does_not_read_cards(self, mocker):
        spy = mocker.patch.object(um, 'worker_kinds')
        assert um.eligible_kinds(_worker(None, _card())) == ()
        spy.assert_not_called()

    def test_live_reading_without_eligible_cards(self):
        assert um.eligible_kinds(_worker(LIVE, _card(ticked=False), _card(state='lost'))) == ()
        assert um.eligible_kinds(_worker(LIVE)) == ()

    def test_returns_a_tuple(self):
        assert type(um.eligible_kinds(_worker(LIVE, _card()))) is tuple
        assert type(um.eligible_kinds(_worker(None, _card()))) is tuple

    def test_cap_still_applies(self):
        cards = [_card(0x7440 + i) for i in range(6)]
        assert len(um.eligible_kinds(_worker(LIVE, *cards))) == um.MAX_KINDS_PER_WORKER


class TestPollWithoutKinds:

    @pytest.mark.parametrize('worker', [_worker(None, _card()), _worker(LIVE), _worker(LIVE, _card(ticked=False))],
                             ids=['no-reading', 'no-cards', 'unticked'])
    def test_only_shots(self, worker):
        assert um.machine_poll_q(worker) == Q(unreal_kind=UnrealKind.SHOTS)


def test_pair_state_values():
    assert (um.FITS, um.DOES_NOT_FIT, um.TESTING, um.FAILED, um.RETEST_DUE, um.NOT_TRIED) == (
        'fits', 'does_not_fit', 'testing', 'failed', 'retest_due', 'not_tried')
