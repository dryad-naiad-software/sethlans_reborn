# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Blender match by backend and PCI address (FR-CB-4, B2; AC-5, AC-6, AC-7).

The slots are built on the worker's path from the detection Blender's ids
(``_card_choice``); the render Blender (fake ``bpy``) may name the same cards
differently, as another Blender version does. Its entries must still be
found by address, and an ignored card it renames must not be reported.
"""
import dataclasses

import pytest

from sethlans_worker_agent.gpu_device_script import GPU_CHECK_EXIT_CODE
from tests.unit.worker._card_choice import (
    KEY_RTX_A, KEY_RTX_B, KEY_XT_03, KEY_XT_23, RTX_A_ID, capacity_for, card, igpu, inv, rtx_a, rtx_b, section,
    xt_03, xt_23,
)
from tests.unit.worker._fake_bpy import CPU, check_failures, list_mismatches, render_reservation

# The render Blender's names for the cards (another version than the detection one).
RTX_A_CUDA = ('CUDA', 'CUDA_NVIDIA GeForce RTX 4080 SUPER_0000:01:00', 'NVIDIA GeForce RTX 4080 SUPER')
RTX_A_OPTIX = ('OPTIX', RTX_A_CUDA[1] + '_OptiX', RTX_A_CUDA[2])
RTX_B_CUDA = ('CUDA', 'CUDA_NVIDIA GeForce RTX 4080 SUPER_0000:02:00', 'NVIDIA GeForce RTX 4080 SUPER')
RTX_B_OPTIX = ('OPTIX', RTX_B_CUDA[1] + '_OptiX', RTX_B_CUDA[2])
XT_03 = ('HIP', 'HIP_AMD Radeon RX 7900 XT (gfx1100)_0000:03:00', 'AMD Radeon RX 7900 XT (gfx1100)')
XT_23 = ('HIP', 'HIP_AMD Radeon RX 7900 XT (gfx1100)_0000:23:00', 'AMD Radeon RX 7900 XT (gfx1100)')
IGPU = ('HIP', 'HIP_AMD Radeon 780M_0000:79:00', 'AMD Radeon 780M')
BLENDER_ALL = [RTX_A_CUDA, RTX_B_CUDA, CPU, RTX_A_OPTIX, RTX_B_OPTIX, XT_03, XT_23, IGPU]


def _all_cards():
    """The inventory of every card in ``BLENDER_ALL`` (as the detection Blender named them)."""
    return (rtx_a(), rtx_b(), xt_03(), xt_23(), igpu())


def _on(spec):
    return (spec[0], spec[1])


def _slot(method, mode, cards, ticked, earlier=0):
    """The reservation of the next GPU job after ``earlier`` GPU jobs were reserved."""
    cap = capacity_for(section(method, mode, ticked, inv(*cards)), inv(*cards))
    for job_id in range(earlier):
        cap.reserve_for_job(100 + job_id, 'GPU')
    return cap.reserve_for_job(238, 'GPU')


def _expect_exit_87(monkeypatch, capsys, reservation, specs):
    with pytest.raises(SystemExit) as caught:
        render_reservation(monkeypatch, reservation, specs)
    assert caught.value.code == GPU_CHECK_EXIT_CODE
    failures = check_failures(capsys.readouterr().err)
    assert len(failures) == 1
    return failures[0]


class TestSplitSlotRenamed:
    """AC-5 and AC-7 with ids renamed by the render Blender."""

    def test_slot_1_finds_its_renamed_entry_and_reports_no_unticked_card(self, monkeypatch, capsys):
        slot = _slot('gpu', 'split', _all_cards(), [KEY_RTX_A, KEY_RTX_B], earlier=1)

        prefs, bpy = render_reservation(monkeypatch, slot, BLENDER_ALL)

        assert slot.card_keys == (KEY_RTX_B,)
        assert prefs.enabled() == [_on(RTX_B_OPTIX)]
        assert prefs.compute_device_type == 'OPTIX'
        assert bpy.context.scene.cycles.device == 'GPU'
        assert capsys.readouterr().err == ''  # unticked XT and iGPU renamed: ignored, not drift

    @pytest.mark.parametrize('address', ['0000:23:00', '0000:23:00.0', '00000000:23:00', '0000:23:00.1'])
    def test_address_written_another_way_is_the_same_card(self, monkeypatch, capsys, address):
        renamed = ('HIP', f'HIP_Radeon RX 7900 XT_{address}', 'Radeon RX 7900 XT')
        slot = _slot('gpu', 'split', (xt_03(), xt_23()), [KEY_XT_03, KEY_XT_23], earlier=1)

        prefs, _bpy = render_reservation(monkeypatch, slot, [CPU, XT_03, renamed])

        assert prefs.enabled() == [_on(renamed)]
        assert capsys.readouterr().err == ''

    def test_upper_case_hex_is_the_same_address(self, monkeypatch, capsys):
        slot = _slot('gpu', 'split', (igpu(),), ['pci:0000:79:00.0'])
        upper = ('HIP', 'HIP_AMD RADEON 780M_0000:79:00'.upper(), 'AMD Radeon 780M')

        prefs, _bpy = render_reservation(monkeypatch, slot, [CPU, upper])

        assert prefs.enabled() == [_on(upper)]
        assert capsys.readouterr().err == ''

    def test_two_entries_of_one_backend_at_the_job_address_exit_87(self, monkeypatch, capsys):
        slot = _slot('gpu', 'split', (xt_03(), xt_23()), [KEY_XT_03, KEY_XT_23], earlier=1)
        twin = ('HIP', 'HIP_AMD Radeon RX 7900 XTX_0000:23:00', 'AMD Radeon RX 7900 XTX')

        line = _expect_exit_87(monkeypatch, capsys, slot, [CPU, XT_03, XT_23, twin])

        assert '2 Blender device entries match' in line
        assert 'same backend at PCI address 0000:23:00' in line

    def test_another_cards_address_is_not_the_job_card(self, monkeypatch, capsys):
        slot = _slot('gpu', 'split', (xt_03(), xt_23()), [KEY_XT_03, KEY_XT_23], earlier=1)

        line = _expect_exit_87(monkeypatch, capsys, slot, [CPU, XT_03])

        assert '0 Blender device entries match' in line

    def test_cuda_and_optix_entries_of_one_card_are_not_two_entries(self, monkeypatch, capsys):
        slot = _slot('gpu', 'split', (rtx_a(),), [KEY_RTX_A])

        prefs, _bpy = render_reservation(monkeypatch, slot, [RTX_A_CUDA, CPU, RTX_A_OPTIX])

        assert prefs.enabled() == [_on(RTX_A_OPTIX)]
        assert capsys.readouterr().err == ''


class TestCombinedRenamed:
    """AC-6: enable set by backend and address; every card in use needs one entry."""

    def test_combined_hip_finds_both_renamed_cards_and_skips_the_ignored_ones(self, monkeypatch, capsys):
        slot = _slot('gpu', 'combined', _all_cards(), [KEY_XT_03, KEY_XT_23])

        prefs, bpy = render_reservation(monkeypatch, slot, BLENDER_ALL)

        assert prefs.compute_device_type == 'HIP'
        assert prefs.enabled() == [_on(XT_03), _on(XT_23)]
        assert bpy.context.scene.cycles.device == 'GPU'
        assert capsys.readouterr().err == ''  # renamed RTX and iGPU are ignored cards

    def test_cuda_and_optix_cards_render_through_their_renamed_cuda_entries(self, monkeypatch, capsys):
        gtx_id = 'CUDA_NVIDIA GeForce GTX 1080_0000:02:00'
        gtx = card('pci:0000:02:00.0', 'NVIDIA GeForce GTX 1080', gtx_id, 'CUDA', 0x10DE, 0x1B80)
        slot = _slot('gpu', 'combined', (rtx_a(), gtx), [KEY_RTX_A, gtx.key])
        gtx_renamed = ('CUDA', 'CUDA_GeForce GTX 1080_0000:02:00', 'GeForce GTX 1080')

        prefs, _bpy = render_reservation(monkeypatch, slot, [RTX_A_CUDA, gtx_renamed, CPU, RTX_A_OPTIX])

        assert prefs.compute_device_type == 'CUDA'
        assert prefs.enabled() == [_on(RTX_A_CUDA), _on(gtx_renamed)]
        assert capsys.readouterr().err == ''

    def test_a_card_in_use_without_an_entry_of_the_backend_exits_87(self, monkeypatch, capsys):
        slot = _slot('gpu', 'combined', (rtx_a(), rtx_b()), [KEY_RTX_A, KEY_RTX_B])

        line = _expect_exit_87(monkeypatch, capsys, slot, [RTX_A_CUDA, RTX_B_CUDA, CPU, RTX_A_OPTIX])

        assert '0 Blender device entries match' in line
        assert 'same backend at PCI address 0000:02:00' in line

    def test_a_card_in_use_with_two_entries_of_the_backend_exits_87(self, monkeypatch, capsys):
        slot = _slot('gpu', 'combined', (xt_03(), xt_23()), [KEY_XT_03, KEY_XT_23])
        twin = ('HIP', 'HIP_Radeon_0000:03:00', 'Radeon')

        line = _expect_exit_87(monkeypatch, capsys, slot, [CPU, XT_03, twin, XT_23])

        assert '2 Blender device entries match' in line
        assert 'same backend at PCI address 0000:03:00' in line

    def test_an_unknown_address_is_reported_and_left_off(self, monkeypatch, capsys):
        slot = _slot('gpu', 'combined', (xt_03(), xt_23()), [KEY_XT_03, KEY_XT_23])
        stranger = ('HIP', 'HIP_AMD Radeon RX 7600_0000:05:00', 'AMD Radeon RX 7600')

        prefs, _bpy = render_reservation(monkeypatch, slot, [CPU, XT_03, XT_23, stranger])

        assert prefs.enabled() == [_on(XT_03), _on(XT_23)]
        err = capsys.readouterr().err
        assert check_failures(err) == []
        mismatches = list_mismatches(err)
        assert len(mismatches) == 1
        assert repr(stranger[1]) in mismatches[0]

    def test_a_card_in_use_wins_over_an_ignored_id_at_its_address(self, monkeypatch, capsys):
        slot = _slot('gpu', 'combined', (rtx_a(), rtx_b()), [KEY_RTX_A, KEY_RTX_B])
        slot = dataclasses.replace(slot, ignored=(RTX_A_ID.removesuffix('_OptiX'),))

        prefs, _bpy = render_reservation(monkeypatch, slot, [RTX_A_CUDA, RTX_B_CUDA, CPU, RTX_A_OPTIX, RTX_B_OPTIX])

        assert prefs.enabled() == [_on(RTX_A_OPTIX), _on(RTX_B_OPTIX)]
        assert capsys.readouterr().err == ''
