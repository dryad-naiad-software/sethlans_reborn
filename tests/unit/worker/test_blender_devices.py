# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The wizard's background Blender device check (FR-CW-2, C-22c, C-19).

``blender_devices._start()`` is called directly (the autouse fixture makes
``start_device_check`` a no-op). The detection is a fake held by an Event,
or the real ``get_gpu_device_details`` with a fake executable and a failing
``subprocess.run``; the OS probe is a fake. The state is read through
``GET /api/setup/worker/cards/`` as the wizard reads it. The hook tests check
that the Blender step starts the check after ``blender_installed``.
"""
import logging
import os
import subprocess
import threading
import types

import pytest

from sethlans_worker_agent import hardware_detection
from sethlans_worker_agent.cards import os_probe
from sethlans_worker_agent.cards.os_probe import OsCard
from sethlans_worker_agent.tool_manager import tool_manager_instance
from sethlans_worker_agent.utils import file_operations
from sethlans_worker_agent.web_ui.setup import blender_download, blender_devices, handlers_blender
from tests.unit.worker._card_choice import AMD, KEY_XT_23, XT_23_ID
from tests.unit.worker._card_pages import SETUP_PATH, call

WAIT = 5.0
FAILED_LINE = "The Blender device check after the Blender step failed."


@pytest.fixture
def no_detection_yet(monkeypatch):
    """An empty detection cache, no check thread, and one fake OS card."""
    monkeypatch.setattr(hardware_detection, '_gpu_details_cache', None)
    monkeypatch.setattr(hardware_detection, '_gpu_details_blender_exe', None)
    monkeypatch.setattr(blender_devices, '_thread', None)
    card = OsCard(key=KEY_XT_23, name='AMD Radeon RX 7900 XT', vendor_id=AMD, device_id=0x744C)
    monkeypatch.setattr(os_probe, 'probe', lambda platform_name=None: (card,))


@pytest.fixture
def held_detection(monkeypatch, tmp_path, no_detection_yet):
    """A fake detection that waits for ``release`` and then fills the cache as a success does."""
    det = types.SimpleNamespace(entered=threading.Event(), release=threading.Event(), calls=[])
    exe = os.path.join(str(tmp_path), 'blender-4.5.0-linux-x64', 'blender')

    def detect():
        det.calls.append(threading.current_thread().name)
        det.entered.set()
        assert det.release.wait(WAIT)
        hardware_detection._gpu_details_blender_exe = exe
        hardware_detection._gpu_details_cache = [{'id': XT_23_ID, 'type': 'HIP', 'name': 'x', 'index': 0}]
        return hardware_detection._gpu_details_cache
    monkeypatch.setattr(hardware_detection, 'get_gpu_device_details', detect)
    return det


def _check_state():
    status, answer = call(SETUP_PATH)
    assert status == 200
    return answer['blender_check']


def _join_check():
    thread = blender_devices._thread
    thread.join(WAIT)
    assert not thread.is_alive()


class TestCheck:

    def test_not_checked_running_then_done(self, held_detection):
        assert _check_state() == 'not_checked'
        assert blender_devices._start() is True
        assert held_detection.entered.wait(WAIT)
        assert blender_devices.check_state() == 'running' and _check_state() == 'running'
        held_detection.release.set()
        _join_check()
        assert blender_devices.check_state() is None
        assert _check_state() == 'done' and call(SETUP_PATH)[1]['blender_version'] == '4.5.0'
        assert held_detection.calls == ['setup-blender-device-check']

    def test_second_start_while_running_starts_nothing(self, held_detection):
        assert blender_devices._start() is True
        assert held_detection.entered.wait(WAIT)
        running = blender_devices._thread
        assert blender_devices._start() is False and blender_devices._thread is running
        held_detection.release.set()
        _join_check()
        assert len(held_detection.calls) == 1

    def test_concurrent_starts_run_one_check(self, held_detection):
        barrier = threading.Barrier(4)
        started = []

        def start():
            barrier.wait(WAIT)
            started.append(blender_devices._start())
        threads = [threading.Thread(target=start, daemon=True) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(WAIT)
            assert not thread.is_alive()
        assert sorted(started) == [False, False, False, True]
        held_detection.release.set()
        _join_check()
        assert len(held_detection.calls) == 1

    def test_filled_cache_starts_nothing(self, held_detection):
        hardware_detection._gpu_details_cache = []
        assert blender_devices._start() is False
        assert blender_devices._thread is None and held_detection.calls == []

    def test_failing_detection_gives_failed_and_a_log_line(self, monkeypatch, tmp_path, no_detection_yet, caplog):
        exe = os.path.join(str(tmp_path), 'blender-4.5.0-linux-x64', 'blender')
        monkeypatch.setattr(hardware_detection, '_find_any_blender_executable', lambda: exe)

        def run(command, **_kwargs):
            raise subprocess.CalledProcessError(1, command)
        monkeypatch.setattr(hardware_detection.subprocess, 'run', run)
        with caplog.at_level(logging.ERROR, logger=hardware_detection.__name__):
            assert blender_devices._start() is True
            _join_check()
        assert _check_state() == 'failed'
        assert hardware_detection._gpu_details_cache == [] and hardware_detection._gpu_details_blender_exe is None
        assert any(r.levelno == logging.ERROR and 'detect_gpus.py' in r.getMessage() for r in caplog.records)
        [only] = call(SETUP_PATH)[1]['cards']
        assert only['note'] == 'Blender could not list its devices; see the log'

    def test_unexpected_error_stays_in_the_thread_and_reads_failed(self, monkeypatch, no_detection_yet, caplog):
        calls = []

        def detect():
            calls.append(1)
            raise RuntimeError('detection broke')
        monkeypatch.setattr(hardware_detection, 'get_gpu_device_details', detect)
        with caplog.at_level(logging.WARNING, logger=blender_devices.__name__):
            assert blender_devices._start() is True
            _join_check()
        [record] = [r for r in caplog.records if r.name == blender_devices.__name__ and r.levelno == logging.WARNING]
        assert record.getMessage() == FAILED_LINE and isinstance(record.exc_info[1], RuntimeError)
        assert blender_devices.check_state() == 'failed'  # the thread has ended: no longer 'running'
        answer = call(SETUP_PATH)[1]
        assert answer['blender_check'] == 'failed'
        assert [c['note'] for c in answer['cards']] == ['Blender could not list its devices; see the log']
        assert hardware_detection._gpu_details_cache is None  # nothing cached: the next Blender step may retry
        assert blender_devices._start() is True
        _join_check()
        assert len(calls) == 2

    def test_a_new_start_clears_the_failed_state(self, monkeypatch, held_detection):
        monkeypatch.setattr(blender_devices, '_failed', True)
        assert _check_state() == 'failed'
        assert blender_devices._start() is True
        assert held_detection.entered.wait(WAIT)
        assert _check_state() == 'running'
        held_detection.release.set()
        _join_check()
        assert _check_state() == 'done'


@pytest.fixture
def order(monkeypatch):
    """Records checkpoints, device check starts and task updates in the order they happen."""
    seen = []
    for module in (handlers_blender, blender_download):
        monkeypatch.setattr(module, 'append_wizard_checkpoint', seen.append)
    monkeypatch.setattr(blender_devices, 'start_device_check', lambda: seen.append('device_check'))
    monkeypatch.setattr(blender_download, 'update_task', lambda _tid, status, **_kw: seen.append(f'task:{status}'))
    return seen


def _tm(tmp_path, exe):
    return types.SimpleNamespace(_resolve_version=lambda _v: '4.5.0', get_blender_executable_path=lambda _v: exe,
                                 _create_tools_directory_if_not_exists=lambda: None, blender_dir=str(tmp_path),
                                 _scan_cache=None)


class TestBlenderStepStartsTheCheck:

    def test_already_installed_request(self, monkeypatch, tmp_path, order):
        monkeypatch.setattr(tool_manager_instance, '_resolve_version', lambda _v: '4.5.0')
        monkeypatch.setattr(tool_manager_instance, 'get_blender_executable_path',
                            lambda _v: os.path.join(str(tmp_path), 'blender'))
        status, answer = call('/api/setup/blender/start-download/', 'POST', {'version': '4.5'})
        assert (status, answer['status']) == (200, 'already_installed')
        assert order == ['blender_installed', 'device_check']

    def test_installed_found_by_the_download_thread(self, tmp_path, order):
        tm = _tm(tmp_path, os.path.join(str(tmp_path), 'blender'))
        assert blender_download._resolve_download_spec('t1', tm, '4.5', threading.Event()) is None
        # checkpoint first; the check starts before "complete" sends the wizard on to the card step
        assert order == ['task:downloading', 'blender_installed', 'device_check', 'task:complete']

    def test_finished_download(self, monkeypatch, tmp_path, order):
        archive = os.path.join(str(tmp_path), 'blender.tar.xz')
        monkeypatch.setattr(file_operations, 'download_file', lambda _url, _dir: archive)
        monkeypatch.setattr(file_operations, 'verify_hash', lambda _path, _hash: True)
        monkeypatch.setattr(file_operations, 'extract_archive', lambda _path, _dir: None)
        monkeypatch.setattr(file_operations, 'cleanup_archive', lambda _path: None)
        blender_download._run_download_pipeline('t1', _tm(tmp_path, None), '4.5.0', 'https://example.invalid/b',
                                                'ab' * 32, threading.Event())
        assert order[-3:] == ['blender_installed', 'device_check', 'task:complete']

    def test_failed_download_starts_no_check(self, monkeypatch, tmp_path, order):
        archive = os.path.join(str(tmp_path), 'blender.tar.xz')
        monkeypatch.setattr(file_operations, 'download_file', lambda _url, _dir: archive)
        monkeypatch.setattr(file_operations, 'verify_hash', lambda _path, _hash: False)
        blender_download._run_download_pipeline('t1', _tm(tmp_path, None), '4.5.0', 'https://example.invalid/b',
                                                'ab' * 32, threading.Event())
        assert 'device_check' not in order and 'blender_installed' not in order
        assert order[-1] == 'task:failed'
