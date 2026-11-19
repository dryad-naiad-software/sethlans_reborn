# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Metal by count (D9, B3; AC-43 macOS part), oneAPI ids by address, and the
helper's address rule against ``cards.identity.blender_pci`` (FR-CB-4).
"""
import sys

import pytest

from sethlans_worker_agent.cards.identity import APPLE_GPU_KEY, blender_pci
from sethlans_worker_agent.gpu_device_script import GPU_CHECK_EXIT_CODE
from sethlans_worker_agent.gpu_select_helper import SELECT_HELPER
from tests.unit.worker._card_choice import capacity_for, card, inv, section
from tests.unit.worker._fake_bpy import (
    ARC_ONEAPI, CPU, METAL_GPU, check_failures, list_mismatches, render_reservation,
)


def _on(spec):
    return (spec[0], spec[1])


def _slot(mode, cards, ticked, earlier=0):
    cap = capacity_for(section('gpu', mode, ticked, inv(*cards)), inv(*cards))
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


class TestMetalByCount:
    """AC-43: on macOS one METAL entry renders; 0 or 2 exit 87."""

    @pytest.fixture
    def slot(self):
        apple = card(APPLE_GPU_KEY, 'Apple M2 Max', METAL_GPU[1], 'METAL')
        return _slot('split', (apple,), [APPLE_GPU_KEY])

    def test_one_metal_entry_renders(self, monkeypatch, capsys, slot):
        prefs, bpy = render_reservation(monkeypatch, slot, [CPU, METAL_GPU])

        assert prefs.compute_device_type == 'METAL'
        assert prefs.enabled() == [_on(METAL_GPU)]
        assert bpy.context.scene.cycles.device == 'GPU'
        assert capsys.readouterr().err == ''

    def test_one_renamed_metal_entry_renders(self, monkeypatch, capsys, slot):
        renamed = ('METAL', 'METAL_Apple M2 Max (GPU 38 cores)', 'Apple M2 Max (GPU 38 cores)')

        prefs, _bpy = render_reservation(monkeypatch, slot, [CPU, renamed])

        assert prefs.enabled() == [_on(renamed)]
        assert capsys.readouterr().err == ''

    def test_no_metal_entry_exits_87(self, monkeypatch, capsys, slot):
        line = _expect_exit_87(monkeypatch, capsys, slot, [CPU])

        assert '0 Blender device entries match' in line
        assert 'Metal is matched by count' in line

    def test_two_metal_entries_exit_87(self, monkeypatch, capsys, slot):
        second = ('METAL', 'METAL_AMD Radeon Pro 5500M', 'AMD Radeon Pro 5500M')

        line = _expect_exit_87(monkeypatch, capsys, slot, [CPU, METAL_GPU, second])

        assert '2 Blender device entries match' in line
        assert 'Metal is matched by count' in line


ARC_A_ID = 'ONEAPI_Intel(R) Arc(TM) A770 Graphics_0000:03:00.0'
ARC_B_ID = 'ONEAPI_Intel(R) Arc(TM) A770 Graphics_10000:01:00.0'
ARC_B = ('ONEAPI', 'ONEAPI_Intel(R) Level-Zero_Intel(R) Arc(TM) A770 Graphics_10000:01:00.0',
         'Intel(R) Arc(TM) A770 Graphics')
KEY_ARC_A, KEY_ARC_B = 'pci:0000:03:00.0', 'pci:10000:01:00.0'


def _arcs():
    return (card(KEY_ARC_A, 'Intel(R) Arc(TM) A770 Graphics', ARC_A_ID, 'ONEAPI', 0x8086, 0x56A0),
            card(KEY_ARC_B, 'Intel(R) Arc(TM) A770 Graphics', ARC_B_ID, 'ONEAPI', 0x8086, 0x56A0))


class TestOneApiByAddress:
    """oneAPI ids end in an address with a function digit; the platform part may differ."""

    def test_split_slot_finds_the_renamed_entry_of_a_five_digit_domain(self, monkeypatch, capsys):
        slot = _slot('split', _arcs(), [KEY_ARC_A, KEY_ARC_B], earlier=1)

        prefs, bpy = render_reservation(monkeypatch, slot, [CPU, ARC_ONEAPI, ARC_B])

        assert slot.card_keys == (KEY_ARC_B,)
        assert prefs.compute_device_type == 'ONEAPI'
        assert prefs.enabled() == [_on(ARC_B)]
        assert bpy.context.scene.cycles.device == 'GPU'
        assert capsys.readouterr().err == ''

    def test_combined_enables_both_renamed_entries(self, monkeypatch, capsys):
        slot = _slot('combined', _arcs(), [KEY_ARC_A, KEY_ARC_B])

        prefs, _bpy = render_reservation(monkeypatch, slot, [CPU, ARC_ONEAPI, ARC_B])

        assert prefs.enabled() == [_on(ARC_ONEAPI), _on(ARC_B)]
        assert capsys.readouterr().err == ''

    def test_an_unticked_renamed_card_is_not_reported(self, monkeypatch, capsys):
        slot = _slot('split', _arcs(), [KEY_ARC_A])

        prefs, _bpy = render_reservation(monkeypatch, slot, [CPU, ARC_ONEAPI, ARC_B])

        assert slot.ignored == (ARC_B_ID,)
        assert prefs.enabled() == [_on(ARC_ONEAPI)]
        assert list_mismatches(capsys.readouterr().err) == []

    def test_two_entries_at_one_address_exit_87(self, monkeypatch, capsys):
        slot = _slot('split', _arcs(), [KEY_ARC_A, KEY_ARC_B])
        twin = ('ONEAPI', 'ONEAPI_Intel(R) OpenCL_Intel(R) Arc(TM) A770 Graphics_0000:03:00.0', 'Arc')

        line = _expect_exit_87(monkeypatch, capsys, slot, [CPU, ARC_ONEAPI, twin, ARC_B])

        assert '2 Blender device entries match' in line
        assert 'same backend at PCI address 0000:03:00' in line


ADDRESS_CORPUS = [
    'CUDA_NVIDIA GeForce RTX 5090_0000:01:00', 'CUDA_NVIDIA GeForce RTX 5090_0000:01:00_OptiX',
    'HIP_AMD Radeon(TM) Graphics_0000:79:00', 'HIP_AMD Radeon RX 7900 XT_0000:23:00.7',
    ARC_A_ID, ARC_B_ID, ARC_ONEAPI[1], METAL_GPU[1], 'CUDA_NVIDIA GeForce RTX 5090_00000000:01:00',
    'HIP_AMD Radeon_0000:0A:1F', 'OPTIX_NVIDIA_0000:02:00_OptiX', 'NVIDIA GeForce RTX 5090',
    'CUDA_NVIDIA GeForce RTX 5090', 'CUDA_NVIDIA GeForce RTX 5090_OptiX', 'CUDA_NVIDIA GeForce RTX 5090_0000:01:00_',
    'CUDA_NVIDIA GeForce RTX 5090_0000:01:00_optix', 'CUDA_NVIDIA_0000:01', 'CUDA_NVIDIA_000:01:00',
    'CUDA_NVIDIA_000000000:01:00', 'CUDA_NVIDIA_0000:001:00', 'CUDA_NVIDIA_0000:01:00.0.0',
    'HIP_AMD Radeon RX 7900 XT_0000:23:00.8', 'CPU', '', '_', 'x_0000:01:00\n',
    'CUDA_NVIDIA_0000:1:00',  # one-digit bus: refused by both
    'HIP_X_ABCD:01:00',       # upper-case domain: accepted by both
]


class TestAddressRuleParity:
    """The helper reads an address exactly as the worker's ``blender_pci`` does."""

    @pytest.fixture(scope='class')
    def helper(self):
        namespace = {'sys': sys}
        exec(compile(SELECT_HELPER, '<select-helper>', 'exec'), namespace)
        return namespace

    @pytest.mark.parametrize('device_id', ADDRESS_CORPUS)
    def test_same_address_as_blender_pci(self, helper, device_id):
        assert helper['_sethlans_pci'](device_id) == blender_pci(device_id)

    def test_one_digit_bus_is_refused_and_upper_case_domain_read(self, helper):
        assert helper['_sethlans_pci']('CUDA_NVIDIA_0000:1:00') is None
        assert helper['_sethlans_pci']('HIP_X_ABCD:01:00') == (0xABCD, 1, 0)

    def test_metal_has_no_address_and_is_one_place(self, helper):
        place = helper['_sethlans_place']
        assert place('METAL', METAL_GPU[1]) == place(None, 'METAL_Apple M3') == ('metal',)
        assert place('HIP', 'METAL_x_0000:01:00') == ('pci', 0, 1, 0)

    def test_an_id_without_address_is_matched_by_itself(self, helper):
        place = helper['_sethlans_place']
        assert place('OPTIX', 'CUDA_GPU_OptiX') == place('CUDA', 'CUDA_GPU') == ('id', 'CUDA_GPU')

    def test_helper_is_pure_ascii(self):
        SELECT_HELPER.encode('ascii')
