# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Generated GPU render script (#238): ASCII-only literals, fail-closed guard,
combined mode across backends, Blender's own device check and device refresh.

Each test generates the real script and executes it against a fake ``bpy``.
"""
import pytest

from sethlans_worker_agent import gpu_device_script
from tests.unit.worker._fake_bpy import (
    CPU, IGPU_HIP, RTX_CUDA, RTX_OPTIX,
    FakePrefs, card, check_failures, generate_script, install_fake_bpy,
    list_mismatches, render, run_script,
)

# A non-RTX NVIDIA card: the worker lists it under CUDA, but Cycles also
# offers an OptiX entry for it.
GTX_CUDA = ('CUDA', 'CUDA_NVIDIA GeForce GTX 1080_0000:02:00', 'NVIDIA GeForce GTX 1080')
GTX_OPTIX = ('OPTIX', GTX_CUDA[1] + '_OptiX', 'NVIDIA GeForce GTX 1080')
BLENDER_RTX_GTX = [RTX_CUDA, GTX_CUDA, CPU, RTX_OPTIX, GTX_OPTIX]
WORKER_RTX_GTX = [card(0, RTX_OPTIX), card(1, GTX_CUDA)]

# Non-ASCII device name and id (registered sign and CJK characters).
INTL_NAME = 'NVIDIA® GeForce RTX 显卡'
INTL_OPTIX = ('OPTIX', 'CUDA_' + INTL_NAME + '_0000:01:00_OptiX', INTL_NAME)
INTL_CUDA = ('CUDA', 'CUDA_' + INTL_NAME + '_0000:01:00', INTL_NAME)


def _key(spec):
    return (spec[0], spec[1])


class RefreshPrefs(FakePrefs):
    """Preferences of a Blender build that has ``refresh_devices()``."""

    def __init__(self, specs, **kwargs):
        super().__init__(specs, **kwargs)
        self.refresh_calls = 0

    def refresh_devices(self):
        self.refresh_calls += 1
        self.devices = [self._make(spec) for spec in self._specs]


class ActivePrefs(FakePrefs):
    """Preferences with Blender's ``has_active_device()`` returning ``value``."""

    def __init__(self, specs, value, **kwargs):
        super().__init__(specs, **kwargs)
        self.value = value
        self.has_active_calls = 0

    def has_active_device(self):
        self.has_active_calls += 1
        return self.value


class FlakyDevicesPrefs(FakePrefs):
    """``devices`` works on the first read after refresh, then raises."""

    def __init__(self, specs, error):
        super().__init__(specs)
        self._error = error
        self._reads = 0
        self._list = []

    @property
    def devices(self):
        self._reads += 1
        if self._reads > 1:
            raise self._error
        return self._list

    @devices.setter
    def devices(self, value):
        self._list = value


class Boom(BaseException):
    """An error that ``except Exception`` does not catch."""


def _run_expecting_check_exit(mocker, monkeypatch, capsys, prefs, cards, gpu_index):
    """Run the script with ``prefs``; assert exit 87; return the marker line."""
    bpy = install_fake_bpy(monkeypatch, prefs)
    script = generate_script(mocker, cards, gpu_index)

    with pytest.raises(SystemExit) as excinfo:
        run_script(script)

    assert excinfo.value.code == gpu_device_script.GPU_CHECK_EXIT_CODE
    assert bpy.context.scene.cycles.device is None
    lines = check_failures(capsys.readouterr().err)
    assert len(lines) == 1
    return lines[0]


class TestAsciiOnlyScript:

    @pytest.mark.parametrize('gpu_index', [0, None])
    def test_non_ascii_card_gives_ascii_script_that_selects_it(
        self, mocker, monkeypatch, capsys, gpu_index,
    ):
        prefs = FakePrefs([INTL_CUDA, CPU, INTL_OPTIX])
        bpy = install_fake_bpy(monkeypatch, prefs)
        script = generate_script(mocker, [card(0, INTL_OPTIX)], gpu_index)

        script.encode('ascii')  # raises if any literal is not ASCII
        run_script(script)

        assert prefs.enabled() == [_key(INTL_OPTIX)]
        assert bpy.context.scene.cycles.device == 'GPU'
        captured = capsys.readouterr()
        captured.out.encode('ascii')
        assert captured.err == ''

    def test_failure_line_for_non_ascii_card_is_ascii(self, mocker, monkeypatch, capsys):
        prefs = FakePrefs([INTL_CUDA, CPU])
        line = _run_expecting_check_exit(
            mocker, monkeypatch, capsys, prefs, [card(0, INTL_OPTIX)], 0,
        )

        line.encode('ascii')
        assert ascii(INTL_OPTIX[1]) in line


class TestFailClosedGuard:

    def test_error_inside_helper_exits_with_marker(self, mocker, monkeypatch, capsys):
        prefs = FlakyDevicesPrefs([RTX_CUDA, CPU, RTX_OPTIX], RuntimeError('device list gone'))

        line = _run_expecting_check_exit(
            mocker, monkeypatch, capsys, prefs, [card(0, RTX_OPTIX)], 0,
        )

        assert 'unexpected error in the GPU device check' in line
        assert "RuntimeError('device list gone')" in line

    def test_base_exception_from_get_devices_exits_with_marker(
        self, mocker, monkeypatch, capsys,
    ):
        prefs = FakePrefs([RTX_CUDA, CPU, RTX_OPTIX])
        mocker.patch.object(prefs, 'get_devices', side_effect=Boom('driver'))

        line = _run_expecting_check_exit(
            mocker, monkeypatch, capsys, prefs, [card(0, RTX_OPTIX)], 0,
        )

        assert "Boom('driver')" in line

    def test_missing_cycles_addon_exits_with_marker(self, mocker, monkeypatch, capsys):
        prefs = FakePrefs([RTX_CUDA, CPU, RTX_OPTIX])
        bpy = install_fake_bpy(monkeypatch, prefs)
        bpy.context.preferences.addons = {}
        script = generate_script(mocker, [card(0, RTX_OPTIX)], 0)

        with pytest.raises(SystemExit) as excinfo:
            run_script(script)

        assert excinfo.value.code == gpu_device_script.GPU_CHECK_EXIT_CODE
        assert bpy.context.scene.cycles.device is None
        lines = check_failures(capsys.readouterr().err)
        assert len(lines) == 1
        assert "KeyError('cycles')" in lines[0]


class TestRtxAndNonRtxCards:
    """Blender [CUDA rtx, CUDA gtx, CPU, OPTIX rtx, OPTIX gtx], worker [OPTIX rtx, CUDA gtx]."""

    def test_combined_enables_both_optix_entries(self, mocker, monkeypatch, capsys):
        prefs, bpy = render(mocker, monkeypatch, WORKER_RTX_GTX, BLENDER_RTX_GTX, None)

        assert prefs.enabled() == [_key(RTX_OPTIX), _key(GTX_OPTIX)]
        assert prefs.compute_device_type == 'OPTIX'
        assert not any(d.use for d in prefs.devices if d.type == 'CUDA')
        assert bpy.context.scene.cycles.device == 'GPU'
        assert capsys.readouterr().err == ''

    def test_isolated_index_1_enables_only_cuda_gtx(self, mocker, monkeypatch, capsys):
        prefs, _bpy = render(mocker, monkeypatch, WORKER_RTX_GTX, BLENDER_RTX_GTX, 1)

        assert prefs.enabled() == [_key(GTX_CUDA)]
        assert prefs.compute_device_type == 'CUDA'
        assert capsys.readouterr().err == ''

    def test_combined_leaves_integrated_hip_off(self, mocker, monkeypatch, capsys):
        cards = WORKER_RTX_GTX + [card(2, IGPU_HIP)]
        blender = BLENDER_RTX_GTX + [IGPU_HIP]

        prefs, _bpy = render(mocker, monkeypatch, cards, blender, None)

        assert prefs.enabled() == [_key(RTX_OPTIX), _key(GTX_OPTIX)]
        assert capsys.readouterr().err == ''

    def test_combined_ignores_optix_entry_of_unlisted_card(
        self, mocker, monkeypatch, capsys,
    ):
        """An entry on no listed card stays off and is reported as drift."""
        prefs, _bpy = render(
            mocker, monkeypatch, [card(0, RTX_OPTIX)], BLENDER_RTX_GTX, None,
        )

        assert prefs.enabled() == [_key(RTX_OPTIX)]
        err = capsys.readouterr().err
        assert check_failures(err) == []
        assert len(list_mismatches(err)) == 2  # the GTX card's CUDA and OPTIX entries


class TestBlenderActiveDeviceCheck:

    def test_has_active_device_false_fails(self, mocker, monkeypatch, capsys):
        prefs = ActivePrefs([RTX_CUDA, CPU, RTX_OPTIX], False)

        line = _run_expecting_check_exit(
            mocker, monkeypatch, capsys, prefs, [card(0, RTX_OPTIX)], 0,
        )

        assert 'has_active_device() is False' in line
        assert prefs.has_active_calls == 1

    def test_has_active_device_true_renders(self, mocker, monkeypatch, capsys):
        prefs = ActivePrefs([RTX_CUDA, CPU, RTX_OPTIX], True)
        bpy = install_fake_bpy(monkeypatch, prefs)

        run_script(generate_script(mocker, [card(0, RTX_OPTIX)], None))

        assert prefs.has_active_calls == 1
        assert bpy.context.scene.cycles.device == 'GPU'
        assert capsys.readouterr().err == ''

    def test_prefs_without_the_method_still_render(self, mocker, monkeypatch):
        assert not hasattr(FakePrefs([]), 'has_active_device')

        prefs, bpy = render(mocker, monkeypatch, [card(0, RTX_OPTIX)], [CPU, RTX_OPTIX], 0)

        assert prefs.enabled() == [_key(RTX_OPTIX)]
        assert bpy.context.scene.cycles.device == 'GPU'


class TestDeviceRefresh:

    def test_refresh_devices_preferred_over_get_devices(self, mocker, monkeypatch):
        prefs = RefreshPrefs([RTX_CUDA, CPU, RTX_OPTIX])
        install_fake_bpy(monkeypatch, prefs)

        run_script(generate_script(mocker, [card(0, RTX_OPTIX)], 0))

        assert prefs.refresh_calls == 1
        assert prefs.get_devices_calls == 0
        assert prefs.enabled() == [_key(RTX_OPTIX)]

    def test_get_devices_used_when_refresh_is_missing(self, mocker, monkeypatch):
        assert not hasattr(FakePrefs([]), 'refresh_devices')

        prefs, _bpy = render(mocker, monkeypatch, [card(0, RTX_OPTIX)], [CPU, RTX_OPTIX], 0)

        assert prefs.get_devices_calls == 1
        assert prefs.enabled() == [_key(RTX_OPTIX)]
