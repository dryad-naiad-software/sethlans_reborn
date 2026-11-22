# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``renderers.unreal_machine.worker_kinds`` (Unreal per-machine memory test
D1, D5, FR-MT-K-3).

The function reads only ``worker.card_status``, so a plain object with that
attribute stands in for the ``Worker`` row (no database).

- The kinds of stored cards that are ``ticked`` true, ``state`` ``ok`` and
  have a vendor and a device id; sorted, distinct.
- At most ``MAX_KINDS_PER_WORKER`` (4), the first ones in sort order.
- Anything not in the stored shape counts as no card: ``()``.
"""
from types import SimpleNamespace

import pytest

from workers.renderers import unreal_machine as um

AMD, NVIDIA = 0x1002, 0x10DE


def _card(vendor=AMD, device=0x744C, revision=0xCC, ticked=True, state='ok', key='pci:0000:03:00.0', **extra):
    return {'key': key, 'name': 'Card', 'vendor_id': vendor, 'device_id': device, 'revision_id': revision,
            'ticked': ticked, 'state': state, **extra}


def _worker(*cards):
    return SimpleNamespace(card_status={'schema': 1, 'cards': list(cards)})


def _kinds(*cards):
    return um.worker_kinds(_worker(*cards))


class TestEligibleCards:

    def test_one_ticked_ok_card(self):
        assert _kinds(_card()) == ('1002:744c:00cc',)

    def test_returns_a_tuple(self):
        assert type(_kinds(_card())) is tuple
        assert type(_kinds()) is tuple

    def test_missing_revision_gives_a_none_kind(self):
        assert _kinds(_card(revision=None)) == ('1002:744c:none',)

    def test_invalid_revision_gives_a_none_kind(self):
        assert _kinds(_card(revision=0x10000)) == ('1002:744c:none',)

    def test_revision_key_absent_gives_a_none_kind(self):
        card = _card()
        del card['revision_id']
        assert _kinds(card) == ('1002:744c:none',)

    def test_identical_cards_give_one_kind(self):
        assert _kinds(_card(key='pci:0000:03:00.0'), _card(key='pci:0000:23:00.0')) == ('1002:744c:00cc',)

    def test_same_model_two_revisions_give_two_kinds(self):
        assert _kinds(_card(revision=0xCC), _card(revision=0xC8)) == ('1002:744c:00c8', '1002:744c:00cc')

    def test_kinds_are_sorted(self):
        cards = (_card(NVIDIA, 0x2704, 0xA1), _card(AMD, 0x744C, 0xCC), _card(AMD, 0x13C0, None))
        assert _kinds(*cards) == ('1002:13c0:none', '1002:744c:00cc', '10de:2704:00a1')


class TestSkippedCards:

    @pytest.mark.parametrize('ticked', [False, None, 1, 'true', 'True'],
                             ids=['false', 'none', 'one', 'str-true', 'str-True'])
    def test_not_ticked_true_is_skipped(self, ticked):
        assert _kinds(_card(ticked=ticked)) == ()

    def test_ticked_key_absent_is_skipped(self):
        card = _card()
        del card['ticked']
        assert _kinds(card) == ()

    @pytest.mark.parametrize('state', ['unticked', 'lost', 'changed', 'blender_lost', 'held', 'unknown',
                                       'OK', ' ok', '', None])
    def test_state_not_ok_is_skipped(self, state):
        assert _kinds(_card(state=state)) == ()

    @pytest.mark.parametrize('vendor, device', [(None, 0x744C), (AMD, None), (None, None), (True, 0x744C),
                                                (AMD, -1), ('1002', 0x744C), (AMD, 0x10000)],
                             ids=['no-vendor', 'no-device', 'no-ids', 'bool-vendor', 'negative-device',
                                  'str-vendor', 'device-over-ffff'])
    def test_card_without_a_valid_vendor_or_device_is_skipped(self, vendor, device):
        assert _kinds(_card(vendor=vendor, device=device)) == ()

    def test_skipped_cards_do_not_hide_the_eligible_one(self):
        cards = (_card(NVIDIA, 0x2704, ticked=False), _card(NVIDIA, 0x2705, state='lost'),
                 _card(vendor=None), 'not a card', None, 7, _card(AMD, 0x744C, 0xCC))
        assert _kinds(*cards) == ('1002:744c:00cc',)


class TestCap:

    def test_constant_is_four(self):
        assert um.MAX_KINDS_PER_WORKER == 4

    def test_four_kinds_are_all_kept(self):
        cards = [_card(AMD, 0x7440 + i) for i in range(4)]
        assert len(_kinds(*cards)) == 4

    def test_five_kinds_keep_the_first_four_sorted(self):
        cards = [_card(AMD, 0x744C, revision) for revision in (0xC5, 0xC1, 0xC4, 0xC2, 0xC3)]
        assert _kinds(*cards) == ('1002:744c:00c1', '1002:744c:00c2', '1002:744c:00c3', '1002:744c:00c4')

    def test_cap_is_on_distinct_kinds_not_cards(self):
        cards = [_card(AMD, 0x744C, 0xCC, key=f'pci:0000:{i:02x}:00.0') for i in range(6)] + [_card(NVIDIA, 0x2704)]
        assert _kinds(*cards) == ('1002:744c:00cc', '10de:2704:00cc')

    def test_cap_counts_only_eligible_cards(self):
        unticked = [_card(AMD, 0x7400 + i, ticked=False) for i in range(8)]
        assert _kinds(*unticked, _card(NVIDIA, 0x2704, 0xA1)) == ('10de:2704:00a1',)

    def test_sixteen_kinds_give_four(self):
        cards = [_card(NVIDIA, 0x2700 + i, 0xA1) for i in range(16)]
        assert _kinds(*reversed(cards)) == tuple(f'10de:{0x2700 + i:04x}:00a1' for i in range(4))


class TestMalformedStatus:

    @pytest.mark.parametrize('status', [None, {}, [], 'cards', 0, {'cards': None}, {'cards': {}},
                                        {'cards': 'x'}, {'cards': ({'ticked': True},)}, {'schema': 1}],
                             ids=['none', 'empty-dict', 'list', 'str', 'zero', 'cards-none', 'cards-dict',
                                  'cards-str', 'cards-tuple', 'no-cards'])
    def test_gives_no_kinds(self, status):
        assert um.worker_kinds(SimpleNamespace(card_status=status)) == ()

    def test_empty_card_list_gives_no_kinds(self):
        assert _kinds() == ()

    def test_card_status_is_not_changed(self):
        worker = _worker(_card(), _card(state='lost'))
        before = repr(worker.card_status)
        um.worker_kinds(worker)
        assert repr(worker.card_status) == before
