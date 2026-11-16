# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Failure paths of the generated GPU render script, run against a fake bpy.

When Blender cannot enable exactly the job's cards, the script must write
one ``SETHLANS_GPU_CHECK_FAILED:`` line to stderr and exit with
``GPU_CHECK_EXIT_CODE`` before the scene is switched to GPU rendering, so
a GPU job never falls back to the CPU silently (#238).
"""
import sys

import pytest

from sethlans_worker_agent import blender_failure, gpu_device_script
from sethlans_worker_agent import capacity as capacity_module
from tests.unit.worker._fake_bpy import (
    BLENDER_MIXED, CPU, IGPU_HIP, RTX_CUDA, RTX_OPTIX,
    FakePrefs, card, check_failures, generate_script, install_fake_bpy,
    run_script,
)

WORKER_MIXED = [card(0, RTX_OPTIX), card(1, IGPU_HIP)]

# Breakout attempts: quotes, a backslash and newlines with a statement that
# would set INJECTED if the strings were pasted into the script unescaped.
HOSTILE_ID = 'CUDA_x\')\nINJECTED = "id"\n#\\"""\'\'\'_OptiX'
HOSTILE_NAME = 'Evil "GPU" \'card\' \\\nINJECTED = "name"\n#\'\'\''
HOSTILE_OPTIX = ('OPTIX', HOSTILE_ID, HOSTILE_NAME)
GPU_CHECK_PREFIX = ('GPU device check failed: Blender did not enable the requested GPU, '
                    'so the render was stopped before it started. ')


def _expect_check_failure(mocker, monkeypatch, capsys, specs, gpu_index, **kwargs):
    """Run the script, assert it failed the device check; return details."""
    prefs = FakePrefs(specs, **kwargs)
    bpy = install_fake_bpy(monkeypatch, prefs)
    script = generate_script(mocker, WORKER_MIXED, gpu_index)

    with pytest.raises(SystemExit) as excinfo:
        run_script(script)

    assert excinfo.value.code == gpu_device_script.GPU_CHECK_EXIT_CODE
    # The script stopped before switching the scene to GPU rendering.
    assert bpy.context.scene.cycles.device is None
    lines = check_failures(capsys.readouterr().err)
    assert len(lines) == 1
    return prefs, lines[0]


class TestJobCardNotUsable:

    def test_missing_job_card_exits_with_check_marker(
        self, mocker, monkeypatch, capsys,
    ):
        """No OPTIX entry (for example no OptiX driver support)."""
        blender = [RTX_CUDA, CPU, IGPU_HIP]

        _prefs, line = _expect_check_failure(mocker, monkeypatch, capsys, blender, 0)

        assert '0 Blender device entries match' in line
        assert f"wanted [OPTIX {RTX_OPTIX[1]!r}]" in line
        assert f"CUDA {RTX_CUDA[1]!r}" in line
        assert f"HIP {IGPU_HIP[1]!r}" in line
        assert 'Blender offers [' in line

    def test_exit_code_is_87(self):
        assert gpu_device_script.GPU_CHECK_EXIT_CODE == 87

    def test_duplicate_entries_for_job_card_fail(self, mocker, monkeypatch, capsys):
        blender = [RTX_CUDA, CPU, RTX_OPTIX, RTX_OPTIX, IGPU_HIP]

        _prefs, line = _expect_check_failure(mocker, monkeypatch, capsys, blender, 0)

        assert '2 Blender device entries match' in line
        assert repr(RTX_OPTIX[1]) in line

    def test_ignored_use_write_on_target_fails_read_back(
        self, mocker, monkeypatch, capsys,
    ):
        stuck = {(RTX_OPTIX[0], RTX_OPTIX[1]): False}

        _prefs, line = _expect_check_failure(
            mocker, monkeypatch, capsys, BLENDER_MIXED, 0, stuck=stuck,
        )

        assert 'enabled [] with compute type OPTIX' in line

    def test_entry_that_cannot_be_disabled_fails_read_back(
        self, mocker, monkeypatch, capsys,
    ):
        """The CUDA entry stays on: the job would not run on its card alone."""
        stuck = {(RTX_CUDA[0], RTX_CUDA[1]): True}

        _prefs, line = _expect_check_failure(
            mocker, monkeypatch, capsys, BLENDER_MIXED, 0, stuck=stuck,
        )

        assert f"enabled [CUDA {RTX_CUDA[1]!r}, OPTIX {RTX_OPTIX[1]!r}]" in line

    def test_rejected_backend_fails_with_exit_87(self, mocker, monkeypatch, capsys):
        """This Blender build does not offer OPTIX as a compute type."""
        prefs, line = _expect_check_failure(
            mocker, monkeypatch, capsys, BLENDER_MIXED, 0, rejected={'OPTIX'},
        )

        assert 'could not select compute backend OPTIX' in line
        assert 'TypeError' in line
        assert prefs.get_devices_calls == 0
        assert not prefs.enabled()

    def test_combined_mode_fails_when_optix_entry_missing(
        self, mocker, monkeypatch, capsys,
    ):
        blender = [RTX_CUDA, CPU, IGPU_HIP]

        _prefs, line = _expect_check_failure(
            mocker, monkeypatch, capsys, blender, None,
        )

        assert '0 Blender device entries match' in line


class _NoisyPrefs(FakePrefs):
    """Native code writes part of a line to stderr while Blender lists the devices."""

    PARTIAL = 'CUDA driver: probing devices without a newline'

    def get_devices(self):
        sys.stderr.write(self.PARTIAL)
        super().get_devices()


class TestMarkerStartsALine:
    """The failure line is recognised after a partial line on stderr."""

    def _failed_stderr(self, mocker, monkeypatch, capsys, prefs):
        install_fake_bpy(monkeypatch, prefs)
        script = generate_script(mocker, WORKER_MIXED, 0)
        with pytest.raises(SystemExit) as excinfo:
            run_script(script)
        assert excinfo.value.code == gpu_device_script.GPU_CHECK_EXIT_CODE
        return capsys.readouterr().err

    def _assert_recognised(self, stderr):
        lines = check_failures(stderr)
        assert len(lines) == 1
        detail = lines[0][len(gpu_device_script.GPU_CHECK_MARKER):].strip()
        message = blender_failure.gpu_check_failure_message(gpu_device_script.GPU_CHECK_EXIT_CODE, stderr)
        assert message == GPU_CHECK_PREFIX + detail
        assert blender_failure.describe_blender_failure(gpu_device_script.GPU_CHECK_EXIT_CODE, stderr) == message
        assert blender_failure.request_recheck_if_check_failed(gpu_device_script.GPU_CHECK_EXIT_CODE, stderr) is True
        assert capacity_module.gpu_list_recheck_pending() is True
        return detail

    def test_partial_line_before_the_marker(self, mocker, monkeypatch, capsys):
        stderr = self._failed_stderr(mocker, monkeypatch, capsys, _NoisyPrefs([RTX_CUDA, CPU, IGPU_HIP]))

        assert stderr.startswith(_NoisyPrefs.PARTIAL + '\n')
        detail = self._assert_recognised(stderr)
        assert detail.startswith('0 Blender device entries match')
        assert _NoisyPrefs.PARTIAL not in detail

    def test_nothing_before_the_marker(self, mocker, monkeypatch, capsys):
        stderr = self._failed_stderr(mocker, monkeypatch, capsys, FakePrefs([RTX_CUDA, CPU, IGPU_HIP]))

        detail = self._assert_recognised(stderr)
        assert detail.startswith('0 Blender device entries match')
        # The only extra output is the empty line before the marker; the
        # message (compared exactly above) ignores it.
        assert stderr.splitlines() == ['', check_failures(stderr)[0]]


class TestHostileStrings:
    """Device ids and names are embedded as literals, never as code."""

    def _run(self, mocker, monkeypatch, gpu_index):
        prefs = FakePrefs([CPU, HOSTILE_OPTIX])
        bpy = install_fake_bpy(monkeypatch, prefs)
        script = generate_script(mocker, [card(0, HOSTILE_OPTIX)], gpu_index)
        compile(script, '<sethlans-render-script>', 'exec')
        namespace = run_script(script)
        return prefs, bpy, script, namespace

    def test_isolated_selects_hostile_card_without_injection(
        self, mocker, monkeypatch, capsys,
    ):
        prefs, bpy, script, namespace = self._run(mocker, monkeypatch, 0)

        assert prefs.enabled() == [('OPTIX', HOSTILE_ID)]
        assert bpy.context.scene.cycles.device == 'GPU'
        assert 'INJECTED' not in namespace
        assert not hasattr(sys.modules['bpy'], 'INJECTED')
        assert not any(
            line.lstrip().startswith('INJECTED') for line in script.splitlines()
        )
        assert capsys.readouterr().err == ''

    def test_combined_selects_hostile_card_without_injection(
        self, mocker, monkeypatch, capsys,
    ):
        prefs, _bpy, _script, namespace = self._run(mocker, monkeypatch, None)

        assert prefs.enabled() == [('OPTIX', HOSTILE_ID)]
        assert 'INJECTED' not in namespace
        assert capsys.readouterr().err == ''

    def test_hostile_card_missing_reports_without_injection(
        self, mocker, monkeypatch, capsys,
    ):
        """The failure line itself carries the hostile id safely (repr)."""
        prefs = FakePrefs([CPU, RTX_OPTIX])
        install_fake_bpy(monkeypatch, prefs)
        script = generate_script(mocker, [card(0, HOSTILE_OPTIX)], 0)
        namespace = {}

        with pytest.raises(SystemExit) as excinfo:
            run_script(script, namespace)

        assert excinfo.value.code == gpu_device_script.GPU_CHECK_EXIT_CODE
        assert 'INJECTED' not in namespace
        lines = check_failures(capsys.readouterr().err)
        assert len(lines) == 1
        assert repr(HOSTILE_ID) in lines[0]
