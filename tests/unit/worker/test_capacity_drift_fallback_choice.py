# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Which installed Blender the periodic GPU check falls back to, and what it logs (#253).

When the Blender that produced the GPU list in use is gone, the check
detects with another installed one: the newest version of the remembered
series, else the newest overall, skipping versions whose executable is
missing. The remembered version is read from the managed install folder
``blender-<version>-<platform>`` in the stored executable path, so after an
adoption the series preference follows the adopted Blender.
"""
import json
import logging
import os
import sys
import threading

import pytest

_saved_argv = sys.argv
sys.argv = ['agent-test-runner']
try:
    from sethlans_worker_agent import agent as agent_module  # noqa: E402
finally:
    sys.argv = _saved_argv

from sethlans_worker_agent import config, hardware_detection  # noqa: E402
from sethlans_worker_agent.capacity import CapacityProfile, WorkerCapacity, drift as drift_module  # noqa: E402
from sethlans_worker_agent.tool_manager import tool_manager_instance  # noqa: E402
from tests.unit.worker.executors._fakes import fake_cards  # noqa: E402

DRIFT = drift_module.__name__
HIP = {'index': 0, 'name': 'AMD Radeon', 'type': 'HIP', 'id': 'HIP_A'}


@pytest.fixture
def installed(mocker, tmp_path):
    """Managed Blenders under tmp_path; ``add(version)`` installs one and returns its executable."""
    paths = {}

    def add(version, exists=True):
        exe = tmp_path / 'blender' / f'blender-{version}-windows-x64' / 'blender.exe'
        if exists:
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_text('')
        paths[version] = str(exe)
        return str(exe)

    mocker.patch.object(tool_manager_instance, 'scan_for_local_blenders',
                        side_effect=lambda: [{'version': v, 'platform': 'windows-x64'} for v in paths])
    mocker.patch.object(tool_manager_instance, 'get_blender_executable_path', side_effect=paths.get)
    add.paths = paths
    return add


class TestChooseFallbackBlender:

    def test_newest_first_compared_numerically(self, installed):
        for version in ('4.5.9', '4.5.16', '4.2.3'):
            installed(version)
        assert drift_module._choose_fallback_blender(None) == installed.paths['4.5.16']

    def test_remembered_series_first(self, installed):
        for version in ('5.0.1', '4.5.9', '4.5.16', '4.2.3'):
            installed(version)
        assert drift_module._choose_fallback_blender('4.5.3') == installed.paths['4.5.16']

    def test_series_not_installed_gives_the_newest_overall(self, installed):
        for version in ('4.5.16', '5.0.1'):
            installed(version)
        assert drift_module._choose_fallback_blender('4.2.3') == installed.paths['5.0.1']

    def test_unusable_versions_are_skipped(self, installed):
        installed('5.1.0')
        installed.paths['5.1.0'] = None  # the tool manager finds no executable
        installed('5.0.1', exists=False)  # listed, but the executable is gone
        installed('4.5.16')
        assert drift_module._choose_fallback_blender(None) == installed.paths['4.5.16']
        assert drift_module._choose_fallback_blender('5.0.0') == installed.paths['4.5.16']

    def test_nothing_usable_gives_none(self, installed):
        assert drift_module._choose_fallback_blender(None) is None
        installed('5.0.1', exists=False)
        assert drift_module._choose_fallback_blender('5.0.1') is None


@pytest.mark.parametrize('suffix', sorted({d['download_suffix'] for d in config.PLATFORM_BLENDER_MAP.values()}))
def test_installed_version_of_managed_paths(suffix):
    layout = next(d for d in config.PLATFORM_BLENDER_MAP.values() if d['download_suffix'] == suffix)
    exe = os.path.join('managed_tools', 'blender', f'blender-4.5.16-{suffix}',
                       *layout['executable_path_in_folder'].split('/'))
    assert drift_module.installed_version_of(exe) == '4.5.16'


@pytest.mark.parametrize('path', [
    None, '', os.path.join('usr', 'bin', 'blender'),
    os.path.join('Blender Foundation', 'Blender 4.5', 'blender.exe'),
    os.path.join('tools', 'blender-latest', 'blender'),
], ids=['none', 'empty', 'system-blender', 'installer-blender', 'no-version'])
def test_installed_version_of_other_paths(path):
    assert drift_module.installed_version_of(path) is None


@pytest.fixture
def gone(monkeypatch, tmp_path):
    """The remembered Blender (a managed 4.2.3) no longer exists."""
    exe = str(tmp_path / 'blender' / 'blender-4.2.3-windows-x64' / 'blender.exe')
    monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', exe)
    return exe


def _lines(caplog, calls):
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger=DRIFT):
        results = [drift_module._detection_executable() for _ in range(calls)]
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO and 'detecting with' in r.getMessage()]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    return results, infos, warnings


class TestOnceOnlyLogging:

    def test_fallback_after_the_warning_still_logs_once(self, installed, gone, caplog):
        results, infos, warnings = _lines(caplog, 2)
        assert results == [None, None]
        assert infos == [] and len(warnings) == 1
        assert 'no usable Blender installation' in warnings[0]

        other = installed('5.0.1')
        results, infos, warnings = _lines(caplog, 3)
        assert results == [other] * 3
        assert len(infos) == 1 and warnings == []
        assert ascii(gone) in infos[0] and ascii(other) in infos[0]

        installed.paths.clear()  # the fallback disappears too: same missing executable
        assert _lines(caplog, 2) == ([None, None], [], [])

    def test_markers_reset_when_the_remembered_blender_is_back(self, installed, gone, caplog):
        _lines(caplog, 1)
        other = installed('5.0.1')
        _lines(caplog, 1)
        os.makedirs(os.path.dirname(gone))
        open(gone, 'w').close()
        assert _lines(caplog, 1) == ([gone], [], [])

        os.remove(gone)
        results, infos, _warnings = _lines(caplog, 1)
        assert results == [other] and len(infos) == 1
        installed.paths.clear()
        assert len(_lines(caplog, 1)[2]) == 1

    def test_markers_reset_after_an_adoption(self, installed, gone, caplog):
        other = installed('5.0.1')
        assert _lines(caplog, 1)[0] == [other]

        drift_module.adopt_detection_executable()

        assert hardware_detection._gpu_details_blender_exe == other
        assert drift_module._fallback_logged is drift_module._NOT_WARNED
        assert drift_module._missing_exe_warned is drift_module._NOT_WARNED
        assert drift_module._fallback_exe is None


def test_series_preference_follows_the_adopted_blender(installed, gone, mocker):
    """4.2.3 gone: 5.0.1 (newest) is adopted; later 5.0.2 is preferred to 5.1.0 and 4.2.9."""
    mocker.patch('sethlans_worker_agent.system_monitor.get_gpu_device_details', return_value=[HIP])
    run = mocker.patch('sethlans_worker_agent.capacity.drift.subprocess.run',
                       return_value=mocker.MagicMock(stdout=json.dumps([HIP]) + '\n'))
    mocker.patch.object(agent_module, '_shutdown_event', threading.Event())
    cap = WorkerCapacity(CapacityProfile(gpu_slot_count=1, cpu_slot_count=1, cpu_thread_ceiling=15,
                                         cpu_threads_effective=15, cards=fake_cards(1), gpu_mode='split'))
    first = installed('5.0.1')
    installed('4.5.16')

    assert cap.assert_gpu_list_unchanged() is None

    assert hardware_detection._gpu_details_blender_exe == first
    assert drift_module.installed_version_of(hardware_detection._gpu_details_blender_exe) == '5.0.1'

    os.remove(first)  # the version sync replaces 5.0.1 with 5.0.2
    del installed.paths['5.0.1']
    for version in ('5.0.2', '5.1.0', '4.2.9'):
        installed(version)

    assert cap.assert_gpu_list_unchanged() is None

    assert run.call_args.args[0][0] == installed.paths['5.0.2']
    assert hardware_detection._gpu_details_blender_exe == installed.paths['5.0.2']
    assert drift_module.get_drift_exit_code() is None
