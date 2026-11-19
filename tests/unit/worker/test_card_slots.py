# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Slots from the saved card choice (FR-CL-2, FR-CL-3, AC-4) and CPU or GPU from
the reservation (FR-CB-1, FR-CB-3, AC-43).

The capacity is built as ``init_capacity`` builds it: a section ticking some
inventory cards, ``matching.evaluate`` + ``card_profile.selection_of``, ``compute_capacity_profile``.
"""
import dataclasses

import pytest

from sethlans_worker_agent import job_processor
from sethlans_worker_agent.capacity.profile import SlotReservation
from sethlans_worker_agent.cards.in_use import COMBINE_PROBLEM_TEXT, NO_TICK_TEXT, NONE_USABLE_TEXT
from sethlans_worker_agent.render_script import NO_CARD_IN_SLOT_TEXT, generate_render_config_script
from tests.unit.worker._card_choice import (
    KEY_IGPU, KEY_RTX_A, KEY_XT_03, KEY_XT_23, XT_03_ID, XT_23_ID, capacity_for, inv, igpu, rtx_a, section, xt_03,
    xt_23,
)

GPU_SELECT = '_sethlans_select_gpus('
CPU_LINE = "bpy.context.scene.cycles.device = 'CPU'"


def _three():
    return inv(xt_03(), xt_23(), igpu())


class TestSlotsFromTheChoice:

    def test_two_of_three_ticked_give_two_slots_in_address_order(self):
        """AC-4."""
        cards = _three()
        cap = capacity_for(section('gpu', 'split', [KEY_XT_23, KEY_XT_03], cards), cards)

        first, second = cap.reserve_for_job(1, 'GPU'), cap.reserve_for_job(2, 'GPU')

        assert (cap.profile.gpu_slot_count, cap.profile.cpu_slot_count) == (2, 0)
        assert cap.profile.card_keys == (KEY_XT_03, KEY_XT_23)
        assert [c.blender_id for c in first.job_cards] == [XT_03_ID]
        assert [c.blender_id for c in second.job_cards] == [XT_23_ID]
        assert cap.reserve_for_job(3, 'GPU') is None
        assert KEY_IGPU not in cap.gpu_assignments_snapshot()

    def test_cpu_and_gpu_keeps_the_cpu_slot(self):
        cards = _three()
        cap = capacity_for(section('cpu_gpu', 'split', [KEY_XT_03], cards), cards)
        assert (cap.profile.gpu_slot_count, cap.profile.cpu_slot_count) == (1, 1)

    def test_method_cpu_has_no_gpu_slot_even_with_ticked_cards(self):
        cards = _three()
        cap = capacity_for(section('cpu', 'split', [KEY_XT_03, KEY_XT_23], cards), cards)
        assert (cap.profile.gpu_slot_count, cap.profile.cpu_slot_count, cap.profile.cards) == (0, 1, ())

    def test_combined_holds_every_card_in_one_slot(self):
        cards = _three()
        cap = capacity_for(section('gpu', 'combined', [KEY_XT_03, KEY_XT_23], cards), cards)

        reservation = cap.reserve_for_job(1, 'ANY')

        assert cap.profile.gpu_slot_count == 1
        assert reservation.card_keys == (KEY_XT_03, KEY_XT_23)
        assert reservation.ignored == (igpu().blender[0],)
        assert cap.is_full() is True

    def test_combined_over_two_families_gives_no_gpu_slot_and_the_problem(self):
        cards = inv(rtx_a(), xt_03())
        cap = capacity_for(section('cpu_gpu', 'combined', [KEY_RTX_A, KEY_XT_03], cards), cards)

        assert (cap.profile.gpu_slot_count, cap.profile.problem) == (0, COMBINE_PROBLEM_TEXT)
        assert cap.free_device_prefs() == ['CPU', 'ANY']
        assert cap.reserve_for_job(1, 'GPU') is None
        assert cap.reserve_for_job(1, 'ANY').problem == COMBINE_PROBLEM_TEXT

    def test_gpu_without_a_tick_has_no_slot_at_all(self):
        cap = capacity_for(section('gpu', 'split', [], _three()), _three())
        assert (cap.profile.total, cap.profile.problem) == (0, NO_TICK_TEXT)
        assert cap.free_device_prefs() == []


class TestReservationCopiesTheCards:

    def test_a_later_profile_does_not_move_a_running_job(self):
        cards = _three()
        cap = capacity_for(section('gpu', 'split', [KEY_XT_03], cards), cards)
        reservation = cap.reserve_for_job(1, 'GPU')

        cap._profile = capacity_for(section('gpu', 'split', [KEY_XT_23], cards), cards).profile

        assert [c.key for c in reservation.cards] == [KEY_XT_03]
        assert [c.key for c in reservation.job_cards] == [KEY_XT_03]

    def test_held_key_of_a_card_no_longer_in_the_profile_does_not_block(self):
        """FR-CL-3: only held keys of cards in the current profile count."""
        cards = _three()
        cap = capacity_for(section('gpu', 'split', [KEY_XT_03], cards), cards)
        cap.reserve_for_job(1, 'GPU')
        assert cap.is_full() is True

        cap._profile = capacity_for(section('gpu', 'split', [KEY_XT_23], cards), cards).profile

        assert cap.is_full() is False
        assert cap.reserve_for_job(2, 'GPU').card_keys == (KEY_XT_23,)

    def test_cpu_reservation_carries_no_card(self):
        cards = _three()
        cap = capacity_for(section('cpu_gpu', 'split', [KEY_XT_03], cards), cards)
        cap.reserve_for_job(1, 'GPU')
        reservation = cap.reserve_for_job(2, 'ANY')
        assert (reservation.device_used, reservation.cards, reservation.job_cards) == ('CPU', (), ())


class TestRenderDevice:
    """CPU or GPU follows ``device_used``; a GPU job never falls back to the CPU."""

    @pytest.fixture
    def combined(self):
        cards = _three()
        return capacity_for(section('cpu_gpu', 'combined', [KEY_XT_03, KEY_XT_23], cards), cards)

    def test_any_job_on_the_combined_slot_renders_on_the_gpu(self, combined):
        """AC-43 (B1): today's code rendered it on the CPU."""
        script = generate_render_config_script(5, 'CYCLES', 'ANY', {}, reservation=combined.reserve_for_job(5, 'ANY'))
        assert GPU_SELECT in script
        assert "'HIP', 'combined'," in script
        assert f"({XT_03_ID!r}, 'HIP')," in script and f"({XT_23_ID!r}, 'HIP')," in script
        assert CPU_LINE not in script

    def test_any_job_on_the_cpu_slot_enables_no_card(self, combined):
        combined.reserve_for_job(5, 'GPU')
        script = generate_render_config_script(6, 'CYCLES', 'ANY', {}, reservation=combined.reserve_for_job(6, 'ANY'))
        assert CPU_LINE in script
        assert GPU_SELECT not in script

    def test_gpu_job_on_a_cpu_reservation_fails_with_the_profile_problem(self):
        """FR-CB-3: method cpu_gpu with no card ticked."""
        cap = capacity_for(section('cpu_gpu', 'split', [], _three()), _three())
        reservation = cap.reserve_for_job(7, 'CPU')
        with pytest.raises(ValueError) as caught:
            generate_render_config_script(7, 'CYCLES', 'GPU', {}, reservation=reservation)
        assert str(caught.value) == NO_TICK_TEXT

    @pytest.mark.parametrize('reservation', [None, SlotReservation(job_id=7, device_used='CPU')])
    def test_gpu_job_without_a_card_fails_with_the_fr_ck_6_text(self, reservation):
        with pytest.raises(ValueError) as caught:
            generate_render_config_script(7, 'CYCLES', 'GPU', {}, reservation=reservation)
        assert str(caught.value) == NONE_USABLE_TEXT

    def test_gpu_slot_without_a_card_fails_with_the_slot_text(self):
        """A whole-machine reservation carries no Blender card and no problem."""
        with pytest.raises(ValueError) as caught:
            generate_render_config_script(7, 'CYCLES', 'GPU', {}, reservation=SlotReservation(job_id=7,
                                                                                              device_used='GPU'))
        assert str(caught.value) == NO_CARD_IN_SLOT_TEXT == "This job's slot holds no graphics card."

    def test_gpu_reservation_whose_job_cards_are_gone_fails(self, combined):
        reservation = dataclasses.replace(combined.reserve_for_job(8, 'GPU'), card_keys=('pci:0000:99:00.0',))
        with pytest.raises(ValueError) as caught:
            generate_render_config_script(8, 'CYCLES', 'GPU', {}, reservation=reservation)
        assert str(caught.value) == NO_CARD_IN_SLOT_TEXT

    @pytest.mark.parametrize('device_used', ['GPU', 'CPU'])
    def test_a_set_problem_wins_over_the_fallback_texts(self, device_used):
        reservation = SlotReservation(job_id=7, device_used=device_used, problem=COMBINE_PROBLEM_TEXT)
        with pytest.raises(ValueError) as caught:
            generate_render_config_script(7, 'CYCLES', 'GPU', {}, reservation=reservation)
        assert str(caught.value) == COMBINE_PROBLEM_TEXT

    @pytest.mark.parametrize('engine', ['BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE', 'WORKBENCH', 'BLENDER_WORKBENCH'])
    def test_other_engines_are_unaffected(self, engine):
        script = generate_render_config_script(9, engine, 'GPU', {}, reservation=None)
        assert 'cycles.device' not in script

    def test_init_capacity_builds_from_the_saved_section(self, mocker):
        """method cpu (the default) probes nothing and gives the CPU slot only."""
        probe = mocker.patch('sethlans_worker_agent.cards.os_probe.probe')
        mocker.patch('sethlans_worker_agent.system_monitor.get_gpu_device_details', return_value=[])
        mocker.patch('sethlans_worker_agent.system_monitor.get_cpu_thread_count', return_value=8)

        cap = job_processor.init_capacity()

        assert (cap.profile.method, cap.profile.gpu_slot_count, cap.profile.cpu_slot_count) == ('cpu', 0, 1)
        probe.assert_not_called()
