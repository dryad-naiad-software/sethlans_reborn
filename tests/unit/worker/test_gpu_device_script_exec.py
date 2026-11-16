# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Execute the generated GPU render script against a fake ``bpy`` (#238).

The worker's GPU index counts physical cards, while Blender's
``prefs.devices`` holds one entry per backend: one RTX card is a CUDA entry
and an OPTIX entry. These tests run the real generated script and check
which entries end up enabled, so a script that picks the wrong entry fails
here even though its text looks plausible.
"""
from tests.unit.worker._fake_bpy import (
    BLENDER_MIXED, CPU, IGPU_HIP, RTX_CUDA, RTX_OPTIX,
    card, check_failures, generate_script, install_fake_bpy, FakePrefs,
    list_mismatches, render, run_script,
)

WORKER_MIXED = [card(0, RTX_OPTIX), card(1, IGPU_HIP)]

# Two cards of the same model on different PCI buses.
RTX_A_CUDA = ('CUDA', 'CUDA_NVIDIA GeForce RTX 4080_0000:01:00', 'NVIDIA GeForce RTX 4080')
RTX_B_CUDA = ('CUDA', 'CUDA_NVIDIA GeForce RTX 4080_0000:02:00', 'NVIDIA GeForce RTX 4080')
RTX_A_OPTIX = ('OPTIX', RTX_A_CUDA[1] + '_OptiX', 'NVIDIA GeForce RTX 4080')
RTX_B_OPTIX = ('OPTIX', RTX_B_CUDA[1] + '_OptiX', 'NVIDIA GeForce RTX 4080')
BLENDER_TWO_RTX = [RTX_A_CUDA, RTX_B_CUDA, CPU, RTX_A_OPTIX, RTX_B_OPTIX]
WORKER_TWO_RTX = [card(0, RTX_A_OPTIX), card(1, RTX_B_OPTIX)]

AMD_A = ('HIP', 'HIP_AMD Radeon RX 7900 XTX_0000:03:00', 'AMD Radeon RX 7900 XTX')
AMD_B = ('HIP', 'HIP_AMD Radeon RX 7900 XTX_0000:04:00', 'AMD Radeon RX 7900 XTX')
BLENDER_TWO_AMD = [CPU, AMD_A, AMD_B]
WORKER_TWO_AMD = [card(0, AMD_A), card(1, AMD_B)]


def _key(spec):
    return (spec[0], spec[1])


class TestMixedBackendList:
    """Blender list [CUDA rtx, CPU, OPTIX rtx, HIP igpu], worker [OPTIX, HIP]."""

    def test_issue_238_index_0_enables_optix_entry_not_cuda(
        self, mocker, monkeypatch, capsys,
    ):
        """Regression for #238: physical card 0 must be the OPTIX entry.

        The old script enabled ``non_cpu_devices[0]``, which is the RTX
        card's CUDA entry, while ``compute_device_type`` was OPTIX, so the
        "GPU" job rendered on the CPU.
        """
        prefs, bpy = render(mocker, monkeypatch, WORKER_MIXED, BLENDER_MIXED, 0)

        assert prefs.enabled() == [_key(RTX_OPTIX)]
        assert prefs.compute_device_type == 'OPTIX'
        cuda_entry = next(d for d in prefs.devices if d.type == 'CUDA')
        assert cuda_entry.use is False
        assert bpy.context.scene.cycles.device == 'GPU'
        assert bpy.context.scene.render.engine == 'CYCLES'
        err = capsys.readouterr().err
        assert check_failures(err) == []
        # The CUDA entry of a card listed under OPTIX is not list drift.
        assert list_mismatches(err) == []

    def test_index_1_enables_only_the_hip_entry(self, mocker, monkeypatch, capsys):
        prefs, bpy = render(mocker, monkeypatch, WORKER_MIXED, BLENDER_MIXED, 1)

        assert prefs.enabled() == [_key(IGPU_HIP)]
        assert prefs.compute_device_type == 'HIP'
        assert bpy.context.scene.cycles.device == 'GPU'
        assert capsys.readouterr().err == ''

    def test_combined_enables_only_optix_entries(self, mocker, monkeypatch, capsys):
        prefs, bpy = render(mocker, monkeypatch, WORKER_MIXED, BLENDER_MIXED, None)

        assert prefs.enabled() == [_key(RTX_OPTIX)]
        assert prefs.compute_device_type == 'OPTIX'
        by_type = {d.type: d.use for d in prefs.devices}
        assert by_type['CUDA'] is False
        assert by_type['HIP'] is False
        assert by_type['CPU'] is False
        assert bpy.context.scene.cycles.device == 'GPU'
        assert capsys.readouterr().err == ''

    def test_get_devices_is_called_after_backend_is_set(self, mocker, monkeypatch):
        prefs, _bpy = render(mocker, monkeypatch, WORKER_MIXED, BLENDER_MIXED, 0)

        assert prefs.get_devices_calls == 1

    def test_isolated_run_prints_the_selected_card(self, mocker, monkeypatch, capsys):
        render(mocker, monkeypatch, WORKER_MIXED, BLENDER_MIXED, 0)

        out = capsys.readouterr().out
        assert 'Isolating GPU: NVIDIA GeForce RTX 4080' in out
        assert RTX_OPTIX[1] in out

    def test_user_settings_still_apply_after_selection(self, mocker, monkeypatch):
        prefs = FakePrefs(BLENDER_MIXED)
        bpy = install_fake_bpy(monkeypatch, prefs)
        script = generate_script(mocker, WORKER_MIXED, 0, {'cycles.samples': 16})

        run_script(script)

        assert bpy.data.scenes[0].cycles.samples == 16
        assert prefs.enabled() == [_key(RTX_OPTIX)]


class TestTwoRtxCards:
    """Blender list [CUDA a, CUDA b, CPU, OPTIX a, OPTIX b]."""

    def test_index_0_enables_optix_a_only(self, mocker, monkeypatch, capsys):
        prefs, _bpy = render(mocker, monkeypatch, WORKER_TWO_RTX, BLENDER_TWO_RTX, 0)

        assert prefs.enabled() == [_key(RTX_A_OPTIX)]
        assert prefs.compute_device_type == 'OPTIX'
        assert capsys.readouterr().err == ''

    def test_index_1_enables_optix_b_only(self, mocker, monkeypatch, capsys):
        prefs, _bpy = render(mocker, monkeypatch, WORKER_TWO_RTX, BLENDER_TWO_RTX, 1)

        assert prefs.enabled() == [_key(RTX_B_OPTIX)]
        assert prefs.compute_device_type == 'OPTIX'
        assert capsys.readouterr().err == ''

    def test_combined_enables_both_optix_entries_and_no_cuda(
        self, mocker, monkeypatch, capsys,
    ):
        prefs, _bpy = render(mocker, monkeypatch, WORKER_TWO_RTX, BLENDER_TWO_RTX, None)

        assert prefs.enabled() == [_key(RTX_A_OPTIX), _key(RTX_B_OPTIX)]
        assert not any(d.use for d in prefs.devices if d.type == 'CUDA')
        assert capsys.readouterr().err == ''


class TestTwoAmdCards:
    """HIP only: Blender list [CPU, HIP a, HIP b]."""

    def test_index_0_enables_card_a(self, mocker, monkeypatch, capsys):
        prefs, _bpy = render(mocker, monkeypatch, WORKER_TWO_AMD, BLENDER_TWO_AMD, 0)

        assert prefs.enabled() == [_key(AMD_A)]
        assert prefs.compute_device_type == 'HIP'
        assert capsys.readouterr().err == ''

    def test_index_1_enables_card_b(self, mocker, monkeypatch, capsys):
        prefs, _bpy = render(mocker, monkeypatch, WORKER_TWO_AMD, BLENDER_TWO_AMD, 1)

        assert prefs.enabled() == [_key(AMD_B)]
        assert prefs.compute_device_type == 'HIP'
        assert capsys.readouterr().err == ''

    def test_combined_enables_both_hip_cards(self, mocker, monkeypatch):
        prefs, _bpy = render(mocker, monkeypatch, WORKER_TWO_AMD, BLENDER_TWO_AMD, None)

        assert prefs.enabled() == [_key(AMD_A), _key(AMD_B)]


class TestCardListDrift:
    """Drift on cards the job does not use is reported, not fatal."""

    def test_missing_non_job_card_reports_one_mismatch(
        self, mocker, monkeypatch, capsys,
    ):
        blender = [RTX_CUDA, CPU, RTX_OPTIX]  # the integrated GPU vanished

        prefs, bpy = render(mocker, monkeypatch, WORKER_MIXED, blender, 0)

        err = capsys.readouterr().err
        mismatches = list_mismatches(err)
        assert len(mismatches) == 1
        assert repr(IGPU_HIP[1]) in mismatches[0]
        assert 'is not in the Blender device list' in mismatches[0]
        assert check_failures(err) == []
        assert prefs.enabled() == [_key(RTX_OPTIX)]
        assert bpy.context.scene.cycles.device == 'GPU'

    def test_extra_blender_card_reports_one_mismatch(
        self, mocker, monkeypatch, capsys,
    ):
        extra = ('HIP', 'HIP_AMD Radeon RX 7600_0000:05:00', 'AMD Radeon RX 7600')
        blender = BLENDER_MIXED + [extra]

        prefs, bpy = render(mocker, monkeypatch, WORKER_MIXED, blender, 0)

        err = capsys.readouterr().err
        mismatches = list_mismatches(err)
        assert len(mismatches) == 1
        assert repr(extra[1]) in mismatches[0]
        assert 'belongs to no card on the worker list' in mismatches[0]
        # The RTX card's CUDA entry is the same card as its OPTIX record.
        assert f"CUDA {RTX_CUDA[1]!r}" not in err
        assert check_failures(err) == []
        assert prefs.enabled() == [_key(RTX_OPTIX)]
        assert bpy.context.scene.cycles.device == 'GPU'
