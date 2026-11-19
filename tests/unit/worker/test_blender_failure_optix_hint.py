# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The OptiX hint in the job error (FR-CB-4): when no OptiX entry matched a job
card, the job error says why that usually happens. Other failures keep
their message unchanged. The stderr comes from the real generated script.
"""
import pytest

from sethlans_worker_agent import blender_failure
from sethlans_worker_agent.blender_failure import OPTIX_HINT, describe_blender_failure, gpu_check_failure_message
from sethlans_worker_agent.gpu_device_script import GPU_CHECK_EXIT_CODE, GPU_CHECK_MARKER
from tests.unit.worker._fake_bpy import (
    CPU, IGPU_HIP, RTX_CUDA, RTX_OPTIX, FakePrefs, card, generate_script, install_fake_bpy, run_script,
)

WORKER_MIXED = [card(0, RTX_OPTIX), card(1, IGPU_HIP)]


def _failed_stderr(mocker, monkeypatch, capsys, specs, gpu_index):
    install_fake_bpy(monkeypatch, FakePrefs(specs))
    with pytest.raises(SystemExit) as caught:
        run_script(generate_script(mocker, WORKER_MIXED, gpu_index))
    assert caught.value.code == GPU_CHECK_EXIT_CODE
    return capsys.readouterr().err


class TestOptixHint:

    @pytest.mark.parametrize('gpu_index', [0, None])  # isolated and combined
    def test_missing_optix_entry_adds_the_hint(self, mocker, monkeypatch, capsys, gpu_index):
        stderr = _failed_stderr(mocker, monkeypatch, capsys, [RTX_CUDA, CPU, IGPU_HIP], gpu_index)

        message = describe_blender_failure(GPU_CHECK_EXIT_CODE, stderr)

        assert '0 Blender device entries match OPTIX' in message
        assert message.endswith(' ' + OPTIX_HINT)
        assert message.count(OPTIX_HINT) == 1

    def test_two_optix_entries_give_no_hint(self, mocker, monkeypatch, capsys):
        stderr = _failed_stderr(mocker, monkeypatch, capsys, [RTX_CUDA, CPU, RTX_OPTIX, RTX_OPTIX, IGPU_HIP], 0)

        message = gpu_check_failure_message(GPU_CHECK_EXIT_CODE, stderr)

        assert '2 Blender device entries match OPTIX' in message
        assert OPTIX_HINT not in message

    def test_missing_hip_entry_gives_no_hint(self, mocker, monkeypatch, capsys):
        stderr = _failed_stderr(mocker, monkeypatch, capsys, [RTX_CUDA, CPU, RTX_OPTIX], 1)

        message = gpu_check_failure_message(GPU_CHECK_EXIT_CODE, stderr)

        assert '0 Blender device entries match HIP' in message
        assert OPTIX_HINT not in message

    def test_hint_survives_a_detail_cut_to_length(self):
        stderr = f"{GPU_CHECK_MARKER} 0 Blender device entries match OPTIX 'x' {'y' * 5000}\n"

        message = gpu_check_failure_message(GPU_CHECK_EXIT_CODE, stderr)

        assert message.endswith(' ' + OPTIX_HINT)

    def test_settings_exit_code_never_gets_the_hint(self):
        stderr = f"{GPU_CHECK_MARKER} 0 Blender device entries match OPTIX 'x'\n"

        message = describe_blender_failure(blender_failure.SETTINGS_SCRIPT_EXIT_CODE, stderr)

        assert OPTIX_HINT not in message
