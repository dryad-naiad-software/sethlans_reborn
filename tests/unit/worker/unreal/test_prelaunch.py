# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared pre-launch check and the per-phase loopback watch (FR-UE-26, FR-UE-27; chunk 7 req. 15; AC-52, AC-53)."""
import pytest

from sethlans_worker_agent.unreal_executor import prelaunch, process_runner, user_state, zen_port
from sethlans_worker_agent.unreal_executor.profile_config import ProfileConfigResult

OK_FOLDERS = user_state.KnownFolderResult(ok=True)
BAD_FOLDERS = user_state.KnownFolderResult(ok=False, reason='Windows shell folders are redirected (Documents ...)')


@pytest.fixture()
def cache(tmp_path):
    path = tmp_path / 'uecache'
    path.mkdir()
    (path / 'trash').mkdir()
    return path


def run(cache, first=True, folders=OK_FOLDERS, config=None, ports=zen_port.PortCheck(ok=True), calls=None):
    calls = [] if calls is None else calls

    def known():
        calls.append('known')
        return folders

    def config_check(profile, trash, merged):
        calls.append('config')
        return config or ProfileConfigResult()

    def port_check(port, cache_dir, worker_sid=None):
        calls.append('port')
        return ports
    return prelaunch.check_before_launch(cache, 8668, ['r.a'], first, known_folders=known,
                                         config_check=config_check, port_check=port_check)


def test_all_checks_pass_and_folders_are_created(cache):
    calls = []
    result = run(cache, calls=calls)
    assert result.ok and calls == ['known', 'config', 'port']
    assert user_state.missing_folders(cache / 'ueprofile') == ()
    assert (cache / 'zen' / 'data').is_dir() and (cache / 'zen' / 'fs').is_dir()


def test_known_folder_check_runs_only_before_the_first_launch(cache):
    calls = []
    assert run(cache, first=False, calls=calls).ok and 'known' not in calls


def test_known_folder_failure_releases_with_dispatch_failed(cache):
    result = run(cache, folders=BAD_FOLDERS)
    assert not result.ok and result.release_reason == 'dispatch_failed' and 'redirected' in result.message
    assert run(cache, folders=None).release_reason == 'dispatch_failed'


@pytest.mark.parametrize('first, reason', [(True, 'dispatch_failed'), (False, 'local_conflict')])
def test_foreign_port_releases(cache, first, reason):
    ports = zen_port.PortCheck(ok=False, foreign=(zen_port.Listener(pid=9, address='0.0.0.0'),))
    result = run(cache, first=first, ports=ports)
    assert not result.ok and result.release_reason == reason and 'PID 9' in result.message


def test_profile_reset_is_reported_and_the_launch_proceeds(cache):
    result = run(cache, config=ProfileConfigResult(reset=1, found_cvars=2, user_cvars=0, findings=['x']))
    assert result.ok and result.profile_reset == 1 and result.user_cvars == 0


def test_failed_reset_releases(cache):
    result = run(cache, first=False, config=ProfileConfigResult(reset=0, findings=['x'], error='busy'))
    assert not result.ok and result.release_reason == 'local_conflict'


def test_a_link_where_the_profile_should_be_releases(cache, monkeypatch):
    def broken(profile):
        raise OSError('ueprofile is a symlink or junction')
    monkeypatch.setattr(prelaunch.user_state, 'ensure_profile', broken)
    result = run(cache)
    assert not result.ok and 'unusable' in result.message


def test_loopback_watch_stops_after_a_passing_sighting():
    results = iter([zen_port.LoopbackCheck(seen=False, ok=True),
                    zen_port.LoopbackCheck(seen=True, ok=True, addresses=('127.0.0.1',))])
    calls = []

    def check(port, cache_dir, worker_sid=None):
        calls.append(port)
        return next(results)
    watch = zen_port.LoopbackWatch(8668, r'D:\uecache', check=check)
    assert watch() is None and watch() is None and watch() is None
    assert len(calls) == 2 and watch.seen


def test_loopback_watch_reports_a_non_loopback_listener():
    def check(port, cache_dir, worker_sid=None):
        return zen_port.LoopbackCheck(seen=True, ok=False, addresses=('0.0.0.0', '127.0.0.1'))
    reason = zen_port.LoopbackWatch(8668, r'D:\uecache', check=check)()
    assert reason.startswith(zen_port.LOOPBACK_DISABLE_REASON) and '0.0.0.0' in reason


def test_phase_vram_sample_asks_for_a_one_tick_old_reading():
    seen = {}

    class Sampler:
        def fresh(self, max_age_s, deadline):
            seen.update(max_age_s=max_age_s, deadline=deadline)
            return 'reading'
    import time
    before = time.monotonic()
    assert process_runner.phase_vram_sample(Sampler())(1.5) == 'reading'
    assert seen['max_age_s'] == 1.0 and before + 1.5 <= seen['deadline'] <= time.monotonic() + 1.5
