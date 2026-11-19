# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``cards.in_use`` (FR-CL-1, FR-CC-2, FR-CC-4, FR-CK-6, D3, D4,
D19, C-17, C-18, B6): which ticked cards Blender renders on, the ignored ids,
the problem text, and Combine GPUs over two backend families.
"""
import dataclasses
import logging

import pytest

from sethlans_worker_agent import config_compute
from sethlans_worker_agent.cards import in_use
from sethlans_worker_agent.cards import inventory as inventory_module
from sethlans_worker_agent.cards.in_use import (
    COMBINE_PROBLEM_TEXT, NO_TICK_TEXT, NONE_USABLE_TEXT, CardInUse, blender_cards_in_use, one_family,
    problem_message, select,
)
from tests.unit.worker._card_choice import (
    IGPU_ID, KEY_IGPU, KEY_RTX_A, KEY_XT_03, KEY_XT_23, RTX_A_ID, XT_03_ID, XT_23_ID, card, detected_ids, igpu,
    inv, rtx_a, section, xt_03, xt_23,
)
from tests.unit.worker.cards._card_ids import blender, os_card


def _alert(kind, key):
    return {'id': 'c' * 32, 'kind': kind, 'key': key, 'name': 'x'}


class TestOneFamily:

    @pytest.mark.parametrize('backends', [('HIP', 'HIP'), ('CUDA', 'OPTIX'), ('OPTIX', 'OPTIX', 'CUDA'),
                                          ('METAL', 'METAL'), ('ONEAPI', None, 'ONEAPI')])
    def test_two_or_more_of_one_family(self, backends):
        assert one_family(backends) is True

    @pytest.mark.parametrize('backends', [('HIP', 'CUDA'), ('OPTIX', 'HIP'), ('ONEAPI', 'METAL'), ('HIP',),
                                          ('HIP', None), (None, None), ()])
    def test_mixed_families_or_fewer_than_two(self, backends):
        assert one_family(backends) is False


class TestBlenderCardsInUse:

    def test_ticked_cards_in_address_order(self):
        """D4: the saved order does not matter; slot i is the i-th card by address."""
        cards = inv(rtx_a(), xt_03(), xt_23(), igpu())
        result = blender_cards_in_use(cards, section('gpu', 'split', [KEY_IGPU, KEY_XT_23, KEY_XT_03], cards))
        assert result == (
            CardInUse(KEY_XT_03, 'AMD Radeon RX 7900 XT', XT_03_ID, 'HIP'),
            CardInUse(KEY_XT_23, 'AMD Radeon RX 7900 XT', XT_23_ID, 'HIP'),
            CardInUse(KEY_IGPU, 'AMD Radeon(TM) Graphics', IGPU_ID, 'HIP'),
        )

    def test_method_cpu_uses_no_card(self):
        cards = inv(xt_03())
        assert blender_cards_in_use(cards, section('cpu', 'split', [KEY_XT_03], cards)) == ()

    def test_unticked_card_is_not_used(self):
        cards = inv(rtx_a(), xt_03())
        assert [c.key for c in blender_cards_in_use(cards, section('gpu', 'split', [KEY_XT_03], cards))] == \
            [KEY_XT_03]

    def test_missing_card_is_not_used(self, caplog):
        caplog.set_level(logging.ERROR, logger='sethlans_worker_agent.cards.in_use')
        sect = section('gpu', 'split', [KEY_XT_03, KEY_XT_23], inv(xt_03(), xt_23()))

        assert [c.key for c in blender_cards_in_use(inv(xt_23()), sect)] == [KEY_XT_23]
        assert any(KEY_XT_03 in r.getMessage() and 'missing from the card list' in r.getMessage()
                   for r in caplog.records)

    def test_same_device_id_with_another_revision_is_not_used(self):
        """C-17: a 7900 XT swapped for an XTX at one address keeps 0x744C but not the revision."""
        sect = section('gpu', 'split', [KEY_XT_03], inv(xt_03(revision_id=0xC8)))
        assert blender_cards_in_use(inv(xt_03(revision_id=0xCC)), sect) == ()

    def test_another_device_id_is_not_used(self):
        sect = section('gpu', 'split', [KEY_XT_03], inv(xt_03()))
        swapped = dataclasses.replace(xt_03(), device_id=0x7448)
        assert blender_cards_in_use(inv(swapped), sect) == ()

    def test_renamed_card_with_the_same_ids_is_used(self):
        sect = section('gpu', 'split', [KEY_XT_03], inv(xt_03()))
        renamed = dataclasses.replace(xt_03(), name='AMD Radeon RX 7900 XT (driver 25.1)')
        assert [c.name for c in blender_cards_in_use(inv(renamed), sect)] == ['AMD Radeon RX 7900 XT (driver 25.1)']

    def test_card_without_ids_counts_as_model_unknown_not_changed(self):
        """C-18: the Blender fallback has no ids and Blender's name; the card stays in use."""
        sect = section('gpu', 'split', [KEY_XT_03], inv(dataclasses.replace(xt_03(), name='AMD GPU 1002:744c')))
        fallback = card(KEY_XT_03, 'AMD Radeon RX 7900 XT', XT_03_ID, 'HIP')
        assert [c.key for c in blender_cards_in_use(inv(fallback), sect)] == [KEY_XT_03]

    def test_saved_record_without_ids_counts_as_model_unknown(self):
        cards = inv(xt_03())
        sect = config_compute.parse({'method': 'gpu', 'cards': [{'key': KEY_XT_03, 'name': 'something else'}]}, {})
        assert [c.key for c in blender_cards_in_use(cards, sect)] == [KEY_XT_03]

    @pytest.mark.parametrize('kind', ['card_lost', 'card_changed', 'card_lost_blender'])
    def test_held_card_is_not_used(self, kind):
        cards = inv(xt_03(), xt_23())
        sect = section('gpu', 'split', [KEY_XT_03, KEY_XT_23], cards, alerts=[_alert(kind, KEY_XT_03)])
        assert [c.key for c in blender_cards_in_use(cards, sect)] == [KEY_XT_23]

    def test_adapter_mismatch_holds_nothing_for_blender(self):
        cards = inv(xt_03())
        sect = section('gpu', 'split', [KEY_XT_03], cards, alerts=[_alert('adapter_mismatch', KEY_XT_03)])
        assert [c.key for c in blender_cards_in_use(cards, sect)] == [KEY_XT_03]

    def test_explicit_alerts_replace_the_saved_ones(self):
        cards = inv(xt_03())
        sect = section('gpu', 'split', [KEY_XT_03], cards, alerts=[_alert('card_lost', KEY_XT_03)])
        assert len(blender_cards_in_use(cards, sect, alerts=[])) == 1

    def test_card_blender_does_not_offer_is_not_used(self):
        cards = inv(card(KEY_XT_03, 'AMD Radeon RX 7900 XT', vendor_id=0x1002, device_id=0x744C,
                         note='not offered by Blender 4.5.0'))
        assert blender_cards_in_use(cards, section('gpu', 'split', [KEY_XT_03], cards)) == ()


class TestSelect:

    def test_ignored_are_the_detected_ids_not_in_use(self):
        cards = inv(rtx_a(), xt_03(), xt_23())
        result = select(section('gpu', 'split', [KEY_XT_03], cards), cards, detected_ids(cards))
        assert [c.key for c in result.cards] == [KEY_XT_03]
        assert result.ignored == (RTX_A_ID, XT_23_ID)
        assert result.problem == ''

    def test_combined_over_two_families_uses_no_card(self, caplog):
        """A hand-edited file or the environment: zero GPU slots and the problem text."""
        caplog.set_level(logging.ERROR, logger='sethlans_worker_agent.cards.in_use')
        cards = inv(rtx_a(), xt_03())

        result = select(section('gpu', 'combined', [KEY_RTX_A, KEY_XT_03], cards), cards, detected_ids(cards))

        assert (result.cards, result.ignored, result.problem) == ((), (), COMBINE_PROBLEM_TEXT)
        assert any('HIP, OPTIX' in r.getMessage() for r in caplog.records if r.levelno == logging.ERROR)

    def test_combined_over_one_family(self):
        cards = inv(rtx_a(), xt_03(), xt_23())
        result = select(section('gpu', 'combined', [KEY_XT_03, KEY_XT_23], cards), cards, detected_ids(cards))
        assert [c.key for c in result.cards] == [KEY_XT_03, KEY_XT_23]
        assert result.ignored == (RTX_A_ID,)

    def test_combined_with_only_one_usable_card_is_not_a_family_problem(self):
        cards = inv(rtx_a(), xt_03())
        result = select(section('gpu', 'combined', [KEY_RTX_A, KEY_XT_03], cards), inv(xt_03()), (XT_03_ID,))
        assert [c.key for c in result.cards] == [KEY_XT_03]
        assert result.problem == ''


class TestProblemMessage:

    def test_cpu_has_no_problem(self):
        assert problem_message(config_compute.parse({'method': 'cpu'}, {}), ()) == ''

    def test_gpu_without_a_ticked_card(self):
        assert problem_message(config_compute.parse({'method': 'gpu'}, {}), ()) == NO_TICK_TEXT

    def test_ticked_cards_none_usable(self):
        cards = inv(xt_03())
        assert problem_message(section('cpu_gpu', 'split', [KEY_XT_03], cards), ()) == NONE_USABLE_TEXT

    def test_a_card_in_use_has_no_problem(self):
        cards = inv(xt_03())
        sect = section('gpu', 'split', [KEY_XT_03], cards)
        assert problem_message(sect, blender_cards_in_use(cards, sect)) == ''


class TestEnvRecords:
    """FR-CC-2: env keys take their model from the first inventory that gives the card's ids."""

    def test_record_without_ids_is_not_frozen(self):
        fallback = card(KEY_XT_23, 'AMD Radeon RX 7900 XT', XT_23_ID, 'HIP')

        first = in_use.env_records((KEY_XT_23,), inv(fallback))
        second = in_use.env_records((KEY_XT_23,), inv(xt_23(revision_id=0xCC)))
        third = in_use.env_records((KEY_XT_23,), inv(xt_23(revision_id=0xC8)))

        assert first == (in_use.record_of(fallback),)
        assert second == third == (in_use.record_of(xt_23(revision_id=0xCC)),)

    def test_key_missing_from_the_inventory_is_a_bare_record(self):
        assert in_use.env_records((KEY_XT_03,), inv(xt_23())) == ({'key': KEY_XT_03, 'name': KEY_XT_03},)

    def test_frozen_record_is_a_copy(self):
        record = in_use.env_records((KEY_XT_03,), inv(xt_03()))[0]
        record['device_id'] = 0
        assert in_use.env_records((KEY_XT_03,), inv(xt_03()))[0]['device_id'] == 0x744C


class TestResolve:

    @pytest.fixture
    def machine(self, mocker):
        """Fake OS probe and Blender cache for the two 7900 XT cards; the join runs as on Linux."""
        state = {'os': (os_card(KEY_XT_03, 'AMD Radeon RX 7900 XT', 0x1002, 0x744C, revision_id=0xC8),
                        os_card(KEY_XT_23, 'AMD Radeon RX 7900 XT', 0x1002, 0x744C, revision_id=0xCC))}
        state['blender'] = [blender(XT_03_ID, 'HIP', 'AMD Radeon RX 7900 XT'),
                            blender(XT_23_ID, 'HIP', 'AMD Radeon RX 7900 XT')]
        probe = mocker.patch.object(in_use.os_probe, 'probe', side_effect=lambda: state['os'])
        mocker.patch('sethlans_worker_agent.cards.startup_log.cached_blender_list',
                     side_effect=lambda: (state['blender'], '4.5.0'))
        real_build = inventory_module.build
        mocker.patch.object(in_use.inventory_module, 'build',
                            side_effect=lambda o, b, v: real_build(o, b, v, platform_name='linux'))
        state['probe'] = probe
        return state

    def test_method_cpu_probes_nothing(self, machine):
        assert in_use.resolve(config_compute.parse({'method': 'cpu'}, {})) == in_use.Selection()
        machine['probe'].assert_not_called()

    def test_env_cards_take_their_model_from_the_first_inventory(self, machine):
        sect = config_compute.parse({}, {config_compute.ENV_METHOD: 'gpu', config_compute.ENV_CARDS: '0000:23:00.0'})

        first = in_use.resolve(sect)
        machine['os'] = (os_card(KEY_XT_23, 'AMD Radeon RX 7900 XTX', 0x1002, 0x744C, revision_id=0xC8),)
        second = in_use.resolve(sect)

        assert [c.key for c in first.cards] == [KEY_XT_23]
        assert first.ignored == (XT_03_ID,)
        assert second.cards == ()
        assert second.problem == NONE_USABLE_TEXT

    def test_env_cards_after_a_blender_only_fallback_still_see_a_later_swap(self, machine):
        """The fallback card has no ids, so it is not frozen; the first full record is."""
        sect = config_compute.parse({}, {config_compute.ENV_METHOD: 'gpu', config_compute.ENV_CARDS: '0000:23:00.0'})
        full = machine['os']

        machine['os'] = None  # the OS probe failed: cards from Blender, no ids
        fallback = in_use.resolve(sect)
        machine['os'] = full
        first_full = in_use.resolve(sect)
        machine['os'] = (os_card(KEY_XT_23, 'AMD Radeon RX 7900 XTX', 0x1002, 0x744C, revision_id=0xC8),)
        swapped = in_use.resolve(sect)

        assert [c.key for c in fallback.cards] == [KEY_XT_23]
        assert [c.key for c in first_full.cards] == [KEY_XT_23]
        assert swapped.cards == ()
        assert swapped.problem == NONE_USABLE_TEXT

    def test_env_combined_over_two_families_gives_the_problem_text(self, machine):
        machine['os'] = machine['os'] + (os_card('pci:0000:41:00.0', 'NVIDIA', 0x10DE, 0x2704),)
        machine['blender'].append(blender('CUDA_NVIDIA GeForce RTX 4080_0000:41:00', 'CUDA', 'RTX 4080'))
        sect = config_compute.parse({}, {config_compute.ENV_METHOD: 'gpu', config_compute.ENV_GPU_MODE: 'combined',
                                         config_compute.ENV_CARDS: '0000:03:00.0,0000:41:00.0'})

        result = in_use.resolve(sect)

        assert (result.cards, result.problem) == ((), COMBINE_PROBLEM_TEXT)
