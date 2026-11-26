# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""One install per Blender version (BS-WK-3).

``blender_install.install`` with the release list, the download and the
extraction faked; ``version_sync`` with ``install`` faked. No network, no
archive. The install checks "installed" with the public
``get_blender_executable_path`` before the lock and with
``_installed_exe_path`` (the plain file check) after it.
"""
import threading
import types

import pytest

from shared.blender_sources import BuildEntry
from sethlans_worker_agent import agent_shutdown, blender_install, blender_wait, version_sync
from sethlans_worker_agent.blender_from_manager import ManagerAnswer, ManagerResult
from sethlans_worker_agent.tool_manager import tool_manager_instance
from sethlans_worker_agent.utils import file_operations

VERSION = '4.5.8'
WAIT = 5.0


@pytest.fixture
def installer(monkeypatch, tmp_path):
    """Fresh version locks; a download that holds until ``release``; extraction marks the version installed."""
    state = types.SimpleNamespace(installed=False, downloads=0, entered=threading.Event(), release=threading.Event())
    exe = str(tmp_path / 'blender-4.5.8-linux-x64' / 'blender')
    monkeypatch.setattr(blender_install, '_version_locks', {})
    monkeypatch.setattr(tool_manager_instance, 'get_blender_executable_path', lambda _v: exe if state.installed else None)
    monkeypatch.setattr(tool_manager_instance, '_installed_exe_path', lambda _v: exe if state.installed else None)
    monkeypatch.setattr(tool_manager_instance, '_create_tools_directory_if_not_exists', lambda: None)
    monkeypatch.setattr(tool_manager_instance, 'blender_dir', tmp_path)
    monkeypatch.setattr(blender_install, 'current_platform_id', lambda: 'linux-x64')
    monkeypatch.setattr(blender_install.blender_from_manager, 'fetch',
                        lambda *_a, **_kw: ManagerResult(ManagerAnswer.UNAVAILABLE))  # BS-WK-4: online next
    state.lookups = 0

    def find(*_a, **_kw):
        state.lookups += 1
        return BuildEntry(VERSION, 'linux-x64', 'blender-4.5.8-linux-x64.tar.xz', 'ab' * 32, 'https://a.test/release/')
    monkeypatch.setattr(blender_install, 'find_build', find)

    def download(_entry, dest_dir, stop=None):
        state.downloads += 1
        state.entered.set()
        assert state.release.wait(WAIT)
        return dest_dir / 'blender-4.5.8-linux-x64.tar.xz'  # never written: nothing to clean up
    monkeypatch.setattr(blender_install, 'download_build', download)
    monkeypatch.setattr(file_operations, 'extract_archive', lambda _a, _d: setattr(state, 'installed', True))
    monkeypatch.setattr(blender_install, '_make_executable', lambda _p: None)
    state.exe = exe
    return state


def test_setup_thread_and_version_sync_together_give_one_download(installer):
    """BS-WK-3: while the setup page's blocking install downloads, version sync's non-blocking call is
    BUSY at once and starts no second download."""
    results = []
    setup = threading.Thread(target=lambda: results.append(blender_install.install(VERSION, blocking=True)))
    setup.start()
    try:
        assert installer.entered.wait(WAIT)

        sync_result = blender_install.install(VERSION, blocking=False)
    finally:
        installer.release.set()
        setup.join(WAIT)

    assert sync_result.outcome is blender_install.InstallOutcome.BUSY
    assert [r.outcome for r in results] == [blender_install.InstallOutcome.INSTALLED]
    assert installer.downloads == 1


def test_a_caller_that_waited_for_the_lock_finds_it_installed(installer, monkeypatch):
    """BS-WK-3: "installed" is checked again after taking the lock, so a second blocking caller does not
    download what the lock's previous holder installed."""
    monkeypatch.setattr(tool_manager_instance, '_installed_exe_path', lambda _v: installer.exe)  # after the lock

    result = blender_install.install(VERSION, blocking=True)

    assert (result.outcome, result.exe_path) == (blender_install.InstallOutcome.INSTALLED, installer.exe)
    assert installer.downloads == 0


@pytest.mark.parametrize('outcome', [blender_install.InstallOutcome.BUSY, blender_install.InstallOutcome.STOPPED],
                         ids=['busy', 'stopped'])
@pytest.mark.parametrize('sync', [
    lambda: version_sync.download_versions([{'series': '4.5', 'version': VERSION}]),
    lambda: version_sync.upgrade_patch_versions([{'series': '4.5', 'version': VERSION}], {'4.5': '4.5.7'}),
], ids=['download', 'upgrade'])
def test_version_sync_skips_a_busy_version_with_no_back_off(monkeypatch, mocker, sync, outcome):
    """BS-WK-3: a BUSY install, or one STOPPED by agent shutdown, is skipped this round: no failure, no
    back-off, nothing removed. The install (through ``blender_wait``) is non-blocking, ends on the agent's
    shutdown event and waits for a fetching manager (BS-WK-10)."""
    install = mocker.patch.object(blender_install, 'install', return_value=blender_install.InstallResult(outcome))
    remove = mocker.patch.object(tool_manager_instance, 'remove_blender_version')

    assert sync() == 0

    install.assert_called_once_with(VERSION, blocking=False, stop=agent_shutdown._shutdown_event,
                                    wait_for_manager=True)
    remove.assert_not_called()
    assert blender_wait.is_in_backoff(VERSION) is False


def test_a_version_being_installed_is_not_reported_and_a_second_install_is_busy(monkeypatch, tmp_path):
    """BS-WK-3: while another install holds the version's lock and marks it downloading, its executable
    may already exist on disk; ``get_blender_executable_path`` still answers None and a non-blocking
    install is BUSY (no caller sees a half-extracted folder as installed)."""
    exe = tmp_path / 'blender-4.5.8-linux-x64' / 'blender'
    exe.parent.mkdir()
    exe.write_bytes(b'')
    monkeypatch.setattr(blender_install, '_version_locks', {})
    monkeypatch.setattr(tool_manager_instance, '_get_platform_identifier', lambda: 'linux-x64')
    monkeypatch.setattr(tool_manager_instance, '_get_executable_path_for_install',
                        lambda name: str(tmp_path / name / 'blender'))
    monkeypatch.setattr(tool_manager_instance, '_downloading_versions', {VERSION})
    other_install = blender_install._version_lock(VERSION)
    other_install.acquire()
    try:
        assert tool_manager_instance._installed_exe_path(VERSION) == str(exe)  # the file is there
        assert tool_manager_instance.get_blender_executable_path(VERSION) is None

        result = blender_install.install(VERSION, blocking=False)
    finally:
        other_install.release()

    assert result.outcome is blender_install.InstallOutcome.BUSY


def test_a_failed_unpack_removes_the_version_folder(installer, monkeypatch, tmp_path):
    """BS-WK-3: an extraction that raises part-way removes ``blender-<version>-<platform>``, so the
    half-written folder never counts as installed, and the install is FAILED with the mark cleared."""
    installer.release.set()
    folder = tmp_path / 'blender-4.5.8-linux-x64'

    def unpack_part_way(_archive, _dest):
        folder.mkdir()
        (folder / 'blender').write_bytes(b'half')
        raise OSError(28, 'No space left on device')
    monkeypatch.setattr(file_operations, 'extract_archive', unpack_part_way)

    result = blender_install.install(VERSION, blocking=True)

    assert result.outcome is blender_install.InstallOutcome.FAILED
    assert 'No space left on device' in result.error
    assert not folder.exists()
    assert VERSION not in tool_manager_instance._downloading_versions


def test_a_blocking_install_waiting_for_the_lock_ends_when_stopped(installer, monkeypatch):
    """BS-WK-3: a blocking install that waits for another holder of the version's lock returns
    STOPPED "Cancelled" once ``stop`` is set (setup page cancel, agent shutdown); it downloads nothing
    and leaves the other holder's lock alone."""
    monkeypatch.setattr(blender_install, '_LOCK_WAIT_STEP_SECONDS', 0.01)
    stop = threading.Event()
    stop.set()
    other_install = blender_install._version_lock(VERSION)
    other_install.acquire()
    try:
        result = blender_install.install(VERSION, blocking=True, stop=stop)
        still_held = other_install.locked()
    finally:
        other_install.release()

    assert (result.outcome, result.error) == (blender_install.InstallOutcome.STOPPED, 'Cancelled')
    assert installer.downloads == 0
    assert still_held


@pytest.mark.parametrize('answer, state, online, outcome', [
    (ManagerAnswer.SUPPLIED, 'ready', 0, blender_install.InstallOutcome.INSTALLED),
    (ManagerAnswer.UNAVAILABLE, 'failed', 1, blender_install.InstallOutcome.INSTALLED),
    (ManagerAnswer.WAIT, 'queued', 0, blender_install.InstallOutcome.WAITING),
], ids=['manager-ready', 'manager-unavailable', 'manager-fetching'])
def test_the_manager_is_asked_before_the_online_sources(installer, monkeypatch, tmp_path, answer, state, online,
                                                        outcome):
    """BS-WK-4, BS-WK-7, BS-WK-10: the manager is asked first. A build it supplies is extracted with no
    online request (release list included); only an unavailable manager sends the install online; a
    fetching manager ends a waiting install at once with WAITING, nothing online and nothing extracted."""
    installer.release.set()
    supplied = tmp_path / 'blender-4.5.8-linux-x64.tar.xz'
    asked, extracted = [], []

    def fetch(version, platform_id, dest_dir, *, stop=None, wait=False):
        asked.append((version, platform_id, dest_dir, wait))
        return ManagerResult(answer, state=state, archive=supplied if answer is ManagerAnswer.SUPPLIED else None)
    monkeypatch.setattr(blender_install.blender_from_manager, 'fetch', fetch)

    def extract(archive, _dest):
        extracted.append(archive)
        installer.installed = True
    monkeypatch.setattr(file_operations, 'extract_archive', extract)

    result = blender_install.install(VERSION, blocking=False, wait_for_manager=True)

    assert (result.outcome, result.manager_state) == (outcome, state)
    assert asked == [(VERSION, 'linux-x64', tmp_path, True)]
    assert (installer.lookups, installer.downloads) == (online, online)
    assert extracted == ([] if outcome is blender_install.InstallOutcome.WAITING else [str(supplied)])
    assert VERSION not in tool_manager_instance._downloading_versions
