# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Card states, notices and alert writes not covered elsewhere (card selection chunk 4).

- AC-9: a ``changed`` card is used by neither renderer and is alerted.
- FR-CK-2 order: ``changed`` wins over ``blender_lost``.
- AC-10: a ``card_new`` notice goes with a save that showed the key and
  stays after a save that did not; the new card is not used either way.
- FR-CK-3, FR-CK-5: ``add_alert`` from two threads at once (the agent loop
  and the Unreal job thread) adds one alert, under ``_section_lock``.
"""
import copy
import dataclasses
import threading

import pytest

from sethlans_worker_agent import config_compute, config_store
from sethlans_worker_agent.cards import store
from sethlans_worker_agent.cards.in_use import NONE_USABLE_TEXT, record_of
from sethlans_worker_agent.cards.matching import BLENDER_LOST, CHANGED, UNTICKED, evaluate
from tests.unit.worker._card_choice import KEY_XT_03, KEY_XT_23, inv, section, xt_03, xt_23

WAIT = 5.0  # bound for every wait and join; never reached when the code is right
NEW_NOTICE = {'kind': 'card_new', 'key': KEY_XT_23, 'name': 'AMD Radeon RX 7900 XT'}


def _alert(kind, key):
    return {'id': 'c' * 32, 'kind': kind, 'key': key, 'name': 'x', 'detail': '', 'raised_at': ''}


@pytest.fixture
def saved(monkeypatch):
    """The saved section in a dict: ``_read_raw`` reads it, ``set_many`` applies each write."""
    state = {'compute': {'method': 'gpu', 'gpu_mode': 'split', 'revision': 1,
                         'cards': [record_of(xt_03())], 'known': [KEY_XT_03]}}
    hooks = {'before_write': None}

    def set_many(pairs):
        if hooks['before_write'] is not None:
            hooks['before_write']()
        for dotted, value in pairs:
            state['compute'][dotted.split('.')[1]] = copy.deepcopy(value)

    monkeypatch.setattr(config_compute, '_read_raw', lambda: copy.deepcopy(state['compute']))
    monkeypatch.setattr(config_store, 'set_many', set_many)
    return state, hooks


class TestChanged:

    def test_changed_card_is_used_by_neither_renderer(self):
        """AC-9."""
        with_index = dataclasses.replace(xt_03(), dxgi_index=0)
        sect = section('gpu', 'split', [KEY_XT_03], inv(with_index))
        now = inv(dataclasses.replace(with_index, device_id=0x7448, name='AMD Radeon RX 7800 XT'))

        result = evaluate(sect, now)

        assert result.states[KEY_XT_03] == CHANGED
        assert (result.blender_cards, result.unreal_cards, result.problem) == ((), (), NONE_USABLE_TEXT)
        assert [(u.kind, u.key) for u in result.unusable] == [('card_changed', KEY_XT_03)]

    def test_changed_wins_over_an_open_blender_alert(self):
        sect = section('gpu', 'split', [KEY_XT_03], inv(xt_03()), alerts=[_alert('card_lost_blender', KEY_XT_03)])
        assert evaluate(sect, inv(dataclasses.replace(xt_03(), device_id=0x7448))).states[KEY_XT_03] == CHANGED

    def test_changed_wins_over_a_fresh_detection_without_it(self):
        sect = section('gpu', 'split', [KEY_XT_03], inv(xt_03()))
        now = inv(dataclasses.replace(xt_03(), device_id=0x7448, blender=None))
        assert evaluate(sect, now, blender_fresh=True, profile_keys=[KEY_XT_03]).states[KEY_XT_03] == CHANGED

    def test_same_model_without_the_fresh_offer_is_blender_lost(self):
        """The control case for the row above."""
        sect = section('gpu', 'split', [KEY_XT_03], inv(xt_03()))
        now = inv(dataclasses.replace(xt_03(), blender=None))
        assert evaluate(sect, now, blender_fresh=True, profile_keys=[KEY_XT_03]).states[KEY_XT_03] == BLENDER_LOST


class TestNewCardNotice:
    """AC-10: the notice follows the keys shown at the last save (D20); the new card is not used."""

    def _check(self, expected_notices):
        result = evaluate(store.snapshot(), inv(xt_03(), xt_23()))
        assert result.notices == expected_notices
        assert result.states[KEY_XT_23] == UNTICKED
        assert [card.key for card in result.blender_cards] == [KEY_XT_03]

    def test_new_address_is_a_notice_and_not_used(self, saved):
        self._check((NEW_NOTICE,))

    def test_save_without_the_key_shown_keeps_the_notice(self, saved):
        store.save('gpu', 'split', [KEY_XT_03], [KEY_XT_03], inv(xt_03(), xt_23()), 1)
        self._check((NEW_NOTICE,))

    def test_save_with_the_key_shown_removes_the_notice(self, saved):
        store.save('gpu', 'split', [KEY_XT_03], [KEY_XT_03, KEY_XT_23], inv(xt_03(), xt_23()), 1)
        self._check(())


class TestAlertFromTwoThreads:

    def test_one_alert_for_one_kind_and_key(self, saved):
        state, hooks = saved
        in_write, go = threading.Event(), threading.Event()

        def first_write_waits():
            if not in_write.is_set():
                in_write.set()
                go.wait(WAIT)
        hooks['before_write'] = first_write_waits
        results = []

        def add():
            results.append(store.add_alert('card_lost', KEY_XT_23, 'AMD Radeon RX 7900 XT', 'missing'))

        first, second = threading.Thread(target=add), threading.Thread(target=add)
        try:
            first.start()
            assert in_write.wait(WAIT)  # the first writer holds _section_lock inside its write
            second.start()
            second.join(0.05)
            assert second.is_alive()  # waits for _section_lock: it cannot read before the first write lands
        finally:
            go.set()
            first.join(WAIT)
            if second.ident is not None:
                second.join(WAIT)

        assert not first.is_alive() and not second.is_alive()
        assert sorted(results) == [False, True]
        assert [(a['kind'], a['key']) for a in state['compute']['alerts']] == [('card_lost', KEY_XT_23)]
