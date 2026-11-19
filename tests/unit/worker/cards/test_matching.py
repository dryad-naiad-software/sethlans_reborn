# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``cards.matching`` (FR-CK-2, FR-CK-4, FR-CK-6, FR-CL-1, D19, C-17, C-18, AC-9, AC-10, AC-11).

Every row of the FR-CK-2 table and its order, the model rules with the PCI
revision (C-17) and without ids (C-18), failed probes and detections, the
notices, the cards to alert for, the problem text and the cards in use.
"""
import dataclasses

import pytest

from sethlans_worker_agent import config_compute
from sethlans_worker_agent.cards import inventory as inventory_module
from sethlans_worker_agent.cards import matching
from sethlans_worker_agent.cards.in_use import COMBINE_PROBLEM_TEXT, NO_TICK_TEXT, NONE_USABLE_TEXT, CardInUse
from sethlans_worker_agent.cards.inventory import Inventory
from sethlans_worker_agent.cards.matching import BLENDER_LOST, CHANGED, HELD, LOST, OK, UNKNOWN, UNTICKED, evaluate
from tests.unit.worker._card_choice import (
    KEY_RTX_A, KEY_XT_03, KEY_XT_23, XT_03_ID, XT_23_ID, card, inv, rtx_a, section, xt_03, xt_23,
)
from tests.unit.worker.cards._card_ids import blender, os_card


def _alert(kind, key, alert_id='c' * 32):
    return {'id': alert_id, 'kind': kind, 'key': key, 'name': 'x', 'detail': '', 'raised_at': ''}


def _two():
    return inv(xt_03(), xt_23())


def _state(sect, inventory, key=KEY_XT_03, **kwargs):
    return evaluate(sect, inventory, **kwargs).states[key]


class TestStateRows:
    """FR-CK-2: one test per row, then the order between rows."""

    def test_lost_when_the_key_is_not_listed(self):
        sect = section('gpu', 'split', [KEY_XT_03], _two())
        assert _state(sect, inv(xt_23())) == LOST

    def test_changed_when_the_device_id_differs(self):
        """AC-9."""
        sect = section('gpu', 'split', [KEY_XT_03], _two())
        assert _state(sect, inv(dataclasses.replace(xt_03(), device_id=0x7448))) == CHANGED

    def test_changed_when_only_the_revision_differs(self):
        """C-17: a 7900 XT and an XTX share 0x744C; the PCI revision tells them apart."""
        sect = section('gpu', 'split', [KEY_XT_03], inv(xt_03(revision_id=0xC8)))
        assert _state(sect, inv(xt_03(revision_id=0xCC))) == CHANGED

    def test_a_missing_revision_on_one_side_compares_without_it(self):
        sect = section('gpu', 'split', [KEY_XT_03], inv(xt_03(revision_id=None)))
        assert _state(sect, inv(xt_03(revision_id=0xCC))) == OK

    @pytest.mark.parametrize('kind', ['card_lost', 'card_changed'])
    def test_held_by_an_open_alert(self, kind):
        sect = section('gpu', 'split', [KEY_XT_03], _two(), alerts=[_alert(kind, KEY_XT_03)])
        assert _state(sect, _two()) == HELD

    def test_blender_lost_by_an_open_alert(self):
        sect = section('gpu', 'split', [KEY_XT_03], _two(), alerts=[_alert('card_lost_blender', KEY_XT_03)])
        assert _state(sect, _two()) == BLENDER_LOST

    def test_blender_lost_when_a_fresh_detection_no_longer_offers_it(self):
        sect = section('gpu', 'split', [KEY_XT_03], _two())
        not_offered = inv(dataclasses.replace(xt_03(), blender=None), xt_23())
        assert _state(sect, not_offered, blender_fresh=True, profile_keys=[KEY_XT_03]) == BLENDER_LOST

    def test_not_blender_lost_from_the_cached_list(self):
        """The GET joins the cached list (``blender_fresh=False``): no detection, no ``blender_lost``."""
        sect = section('gpu', 'split', [KEY_XT_03], _two())
        not_offered = inv(dataclasses.replace(xt_03(), blender=None))
        assert _state(sect, not_offered, profile_keys=[KEY_XT_03]) == OK

    def test_not_blender_lost_when_the_profile_never_had_it(self):
        """A card Blender never offered (Unreal only) stays ``ok``."""
        sect = section('gpu', 'split', [KEY_XT_03], _two())
        not_offered = inv(dataclasses.replace(xt_03(), blender=None))
        assert _state(sect, not_offered, blender_fresh=True, profile_keys=()) == OK

    def test_ok(self):
        sect = section('gpu', 'split', [KEY_XT_03], _two())
        assert _state(sect, _two()) == OK

    def test_unticked_inventory_cards(self):
        sect = section('gpu', 'split', [KEY_XT_03], _two())
        assert _state(sect, _two(), key=KEY_XT_23) == UNTICKED

    def test_adapter_mismatch_holds_nothing(self):
        sect = section('gpu', 'split', [KEY_XT_03], _two(), alerts=[_alert('adapter_mismatch', KEY_XT_03)])
        assert _state(sect, _two()) == OK

    def test_lost_wins_over_an_open_alert(self):
        sect = section('gpu', 'split', [KEY_XT_03], _two(), alerts=[_alert('card_changed', KEY_XT_03)])
        assert _state(sect, inv(xt_23())) == LOST

    def test_changed_wins_over_held(self):
        sect = section('gpu', 'split', [KEY_XT_03], _two(), alerts=[_alert('card_lost', KEY_XT_03)])
        assert _state(sect, inv(dataclasses.replace(xt_03(), device_id=0x7448))) == CHANGED

    def test_held_wins_over_blender_lost(self):
        alerts = [_alert('card_lost', KEY_XT_03), _alert('card_lost_blender', KEY_XT_03, 'd' * 32)]
        sect = section('gpu', 'split', [KEY_XT_03], _two(), alerts=alerts)
        assert _state(sect, _two()) == HELD

    def test_states_do_not_depend_on_the_method(self):
        """C-23: ticks bind Unreal whatever the method."""
        sect = section('cpu', 'split', [KEY_XT_03], _two())
        assert _state(sect, inv(xt_23())) == LOST


class TestModelUnknown:
    """C-18: no ids on one side is "model unknown, keep the state", never ``changed``."""

    def test_fallback_card_without_ids_and_another_name(self):
        sect = section('gpu', 'split', [KEY_XT_03], inv(dataclasses.replace(xt_03(), name='AMD GPU 1002:744c')))
        fallback = card(KEY_XT_03, 'AMD Radeon RX 7900 XT', XT_03_ID, 'HIP')
        assert _state(sect, Inventory(cards=(fallback,), os_probe_failed=True)) == OK

    def test_saved_record_without_ids(self):
        sect = config_compute.parse({'method': 'gpu', 'cards': [{'key': KEY_XT_03, 'name': 'something else'}]}, {})
        assert _state(sect, _two()) == OK

    def test_fallback_card_keeps_a_hold(self):
        sect = section('gpu', 'split', [KEY_XT_03], _two(), alerts=[_alert('card_lost', KEY_XT_03)])
        fallback = card(KEY_XT_03, 'AMD Radeon RX 7900 XT', XT_03_ID, 'HIP')
        assert _state(sect, Inventory(cards=(fallback,), os_probe_failed=True)) == HELD

    def test_key_absent_from_a_blender_built_inventory_is_unknown_not_lost(self):
        sect = section('gpu', 'split', [KEY_XT_03, KEY_RTX_A], inv(xt_03(), rtx_a()))
        fallback = Inventory(cards=(card(KEY_XT_03, 'AMD Radeon RX 7900 XT', XT_03_ID, 'HIP'),), os_probe_failed=True)
        result = evaluate(sect, fallback)
        assert result.states[KEY_RTX_A] == UNKNOWN
        assert result.unusable == ()


class TestFailed:
    """FR-CK-2 last sentence, AC-11: a failed probe or detection changes nothing."""

    @pytest.mark.parametrize('inventory, blender_fresh', [(None, False), (None, True), ('two', None)])
    def test_failed_result(self, inventory, blender_fresh):
        sect = section('gpu', 'split', [KEY_XT_03], _two(), alerts=[_alert('card_lost', KEY_XT_03)])
        result = evaluate(sect, _two() if inventory == 'two' else None, blender_fresh=blender_fresh)
        assert result.failed is True
        assert (result.unusable, result.notices, result.blender_cards, result.unreal_cards) == ((), (), (), ())
        assert (result.states, result.problem) == ({}, '')


class TestUnusable:

    def test_each_unusable_card_with_its_alert_kind_and_detail(self):
        cards = inv(xt_03(), xt_23(), rtx_a())
        sect = section('gpu', 'split', [KEY_RTX_A, KEY_XT_03, KEY_XT_23], cards)
        now = Inventory(cards=(dataclasses.replace(xt_03(), blender=None),
                               dataclasses.replace(xt_23(), device_id=0x7448, name='AMD Radeon RX 7800 XT')),
                        blender_version='4.5.0')

        result = evaluate(sect, now, blender_fresh=True, profile_keys=[KEY_XT_03, KEY_XT_23, KEY_RTX_A])

        assert [(u.kind, u.key, u.state) for u in result.unusable] == [
            ('card_lost', KEY_RTX_A, LOST), ('card_lost_blender', KEY_XT_03, BLENDER_LOST),
            ('card_changed', KEY_XT_23, CHANGED)]
        assert [u.detail for u in result.unusable] == [
            'missing from the card list', 'not offered by Blender 4.5.0 any more',
            'was AMD Radeon RX 7900 XT, now AMD Radeon RX 7800 XT']
        assert result.unusable[0].name == 'NVIDIA GeForce RTX 4080'

    def test_held_and_ok_cards_are_not_unusable(self):
        sect = section('gpu', 'split', [KEY_XT_03, KEY_XT_23], _two(), alerts=[_alert('card_lost', KEY_XT_03)])
        assert evaluate(sect, _two()).unusable == ()


class TestNotices:
    """FR-CK-4, AC-10."""

    def test_new_card_not_known(self):
        sect = config_compute.parse({'known': [KEY_XT_03]}, {})
        result = evaluate(sect, _two())
        assert result.notices == ({'kind': 'card_new', 'key': KEY_XT_23, 'name': 'AMD Radeon RX 7900 XT'},)

    def test_known_unticked_card_gone(self):
        sect = config_compute.parse({'known': [KEY_XT_03, KEY_XT_23, KEY_RTX_A]}, {})
        assert evaluate(sect, _two()).notices == ({'kind': 'card_gone', 'key': KEY_RTX_A, 'name': KEY_RTX_A},)

    def test_known_ticked_card_gone_is_lost_not_a_notice(self):
        raw = {'method': 'gpu', 'known': [KEY_XT_03, KEY_XT_23],
               'cards': [{'key': KEY_XT_03, 'vendor_id': 0x1002, 'device_id': 0x744C, 'name': 'x'}]}
        result = evaluate(config_compute.parse(raw, {}), inv(xt_23()))
        assert result.notices == ()
        assert result.states[KEY_XT_03] == LOST

    def test_no_gone_notice_from_a_blender_built_inventory(self):
        sect = config_compute.parse({'known': [KEY_XT_03, KEY_XT_23]}, {})
        fallback = Inventory(cards=(card(KEY_XT_23, 'AMD Radeon RX 7900 XT', XT_23_ID, 'HIP'),), os_probe_failed=True)
        assert evaluate(sect, fallback).notices == ()


class TestInUseAndProblem:

    def test_blender_cards_are_the_ok_ones_with_a_blender_match(self):
        sect = section('gpu', 'split', [KEY_XT_03, KEY_XT_23], _two(), alerts=[_alert('card_lost', KEY_XT_23)])
        result = evaluate(sect, _two())
        assert result.blender_cards == (CardInUse(KEY_XT_03, 'AMD Radeon RX 7900 XT', XT_03_ID, 'HIP'),)
        assert result.problem == ''

    def test_unreal_cards_are_ok_or_blender_lost_with_a_dxgi_index(self):
        a, b = dataclasses.replace(xt_03(), dxgi_index=0), dataclasses.replace(xt_23(), dxgi_index=1)
        no_index = dataclasses.replace(rtx_a(), dxgi_index=None)
        cards = inv(a, b, no_index)
        alerts = [_alert('card_lost_blender', KEY_XT_03)]
        result = evaluate(section('cpu', 'split', [KEY_XT_03, KEY_XT_23, KEY_RTX_A], cards, alerts=alerts), cards)
        assert [c.key for c in result.unreal_cards] == [KEY_XT_03, KEY_XT_23]
        assert result.blender_cards == ()

    def test_held_card_is_used_by_neither(self):
        a = dataclasses.replace(xt_03(), dxgi_index=0)
        sect = section('gpu', 'split', [KEY_XT_03], inv(a), alerts=[_alert('card_changed', KEY_XT_03)])
        result = evaluate(sect, inv(a))
        assert (result.blender_cards, result.unreal_cards, result.problem) == ((), (), NONE_USABLE_TEXT)

    def test_gpu_without_a_ticked_card(self):
        assert evaluate(config_compute.parse({'method': 'gpu'}, {}), _two()).problem == NO_TICK_TEXT

    def test_combined_over_two_families(self):
        cards = inv(rtx_a(), xt_03())
        result = evaluate(section('gpu', 'combined', [KEY_RTX_A, KEY_XT_03], cards), cards)
        assert (result.blender_cards, result.problem) == ((), COMBINE_PROBLEM_TEXT)

    def test_env_cards_are_evaluated_with_their_frozen_records(self):
        sect = config_compute.parse({}, {config_compute.ENV_METHOD: 'gpu', config_compute.ENV_CARDS: '0000:23:00.0'})
        first = evaluate(sect, _two())
        swapped = evaluate(sect, inv(dataclasses.replace(xt_23(), revision_id=0xC8)))
        assert first.states[KEY_XT_23] == OK and first.section.cards[0]['device_id'] == 0x744C
        assert swapped.states[KEY_XT_23] == CHANGED


class TestMacos:
    """D9: ``lost`` on macOS is "no single Metal device"; the inventory then lists no ``apple-gpu``."""

    def test_two_metal_devices_make_apple_gpu_lost(self):
        sect = config_compute.parse({'method': 'gpu', 'cards': [{'key': 'apple-gpu', 'name': 'Apple M2 Max'}]}, {})
        built = inventory_module.build((os_card(None, 'Apple M2 Max'),),
                                       [blender('METAL_a', 'METAL', 'a'), blender('METAL_b', 'METAL', 'b')],
                                       '4.5.0', platform_name='darwin')
        assert evaluate(sect, built).states['apple-gpu'] == LOST

    def test_one_metal_device_is_ok(self):
        sect = config_compute.parse({'method': 'gpu', 'cards': [{'key': 'apple-gpu', 'name': 'Apple M2 Max'}]}, {})
        built = inventory_module.build((os_card(None, 'Apple M2 Max'),), [blender('METAL_a', 'METAL', 'Apple M2 Max')],
                                       '4.5.0', platform_name='darwin')
        result = evaluate(sect, built)
        assert result.states['apple-gpu'] == OK and [c.key for c in result.blender_cards] == ['apple-gpu']


def test_card_states_with_explicit_alerts():
    sect = section('gpu', 'split', [KEY_XT_03], _two(), alerts=[_alert('card_lost', KEY_XT_03)])
    assert matching.card_states(sect, _two(), alerts=[])[KEY_XT_03] == OK
