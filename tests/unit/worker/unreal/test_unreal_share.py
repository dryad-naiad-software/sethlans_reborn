# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The worker's own Sethlans share (Unreal foundation FR-WC-7, D3b/Q3,
Conc R2-M2/M3, Sec N4; FR-T-5 "Share").
"""
import os
import stat
import threading
import types

import pytest

from sethlans_worker_agent import unreal_share
from shared.unreal.unc import validate_unc_dir

ROOT = validate_unc_dir('\\\\NAS.lan\\sethlans\\')  # a trailing backslash is stripped


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def dir_stat(path):
    return types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755)


def file_stat(path):
    return types.SimpleNamespace(st_mode=stat.S_IFREG | 0o644)


def monitor(clock=None, stat_fn=dir_stat, base=60, ceiling=900, timeout=5):
    return unreal_share.ShareMonitor(ROOT, base, ceiling, timeout, clock=clock or Clock(), stat_fn=stat_fn)


@pytest.fixture(autouse=True)
def _reset():
    unreal_share.reset_for_tests()
    yield
    unreal_share.reset_for_tests()


class TestConfigure:

    def test_empty_means_not_configured(self):
        unreal_share.configure('', 60, 900, 5)
        assert unreal_share.state() == unreal_share.NOT_CONFIGURED

    def test_invalid_raises_with_the_rule_message(self):
        with pytest.raises(ValueError, match='rule 5'):
            unreal_share.configure(r'\\nas\C$', 60, 900, 5)
        with pytest.raises(ValueError, match='rule 7'):
            unreal_share.configure(r'D:\share', 60, 900, 5)

    def test_valid_is_canonical_and_unreachable_until_checked(self):
        unreal_share.configure('\\\\NAS.lan\\Sethlans\\Projects\\', 60, 900, 5)
        current = unreal_share.state()
        assert current.root.canonical == r'\\nas.lan\Sethlans\Projects'
        assert current.reachable is False and current.checked_at is None

    def test_single_label_host_warns(self, caplog):
        unreal_share.configure(r'\\alpha\sethlans', 60, 900, 5)
        assert unreal_share.SINGLE_LABEL_WARNING in caplog.text
        caplog.clear()
        unreal_share.configure(r'\\alpha.lan\sethlans', 60, 900, 5)
        assert unreal_share.SINGLE_LABEL_WARNING not in caplog.text

    def test_mark_unreachable_without_root_is_harmless(self):
        unreal_share.mark_unreachable('copy stalled')
        assert unreal_share.state() == unreal_share.NOT_CONFIGURED


class TestChecks:

    def run_check(self, mon):
        token = mon.begin_check()
        assert token is not None
        ok, error = mon.check_now()
        mon.finish_check(token, ok, error)
        return ok, error

    def test_directory_is_reachable(self, caplog):
        caplog.set_level('INFO', logger='sethlans_worker_agent.unreal_share')
        mon = monitor()
        assert self.run_check(mon) == (True, '')
        assert mon.state().reachable is True
        assert r'Sethlans share \\nas.lan\sethlans reachable.' in caplog.text

    def test_non_directory_is_unreachable(self):
        mon = monitor(stat_fn=file_stat)
        assert self.run_check(mon) == (False, 'not a directory')
        assert mon.state().reachable is False

    def test_stat_error_is_unreachable(self):
        def missing(path):
            raise FileNotFoundError(2, 'gone')

        mon = monitor(stat_fn=missing)
        ok, error = self.run_check(mon)
        assert ok is False and 'FileNotFoundError' in error

    def test_timeout_is_unreachable_and_blocks_the_next_check(self):
        release = threading.Event()
        clock = Clock()

        def blocked(path):
            release.wait(5)
            return dir_stat(path)

        mon = monitor(clock=clock, stat_fn=blocked, timeout=0.05)
        ok, error = self.run_check(mon)
        assert ok is False and 'no answer within' in error
        clock.now += 10_000
        assert mon.begin_check() is None  # previous stat still in flight
        release.set()
        for _ in range(200):
            token = mon.begin_check()
            if token is not None:
                break
            threading.Event().wait(0.01)
        assert token is not None

    def test_not_due_yet(self):
        clock = Clock()
        mon = monitor(clock=clock)
        self.run_check(mon)
        assert mon.begin_check() is None
        clock.now += 60
        assert mon.begin_check() is not None


class TestBackoffAndStaleness:

    def test_failures_double_the_interval_up_to_the_ceiling_and_success_resets(self):
        clock = Clock()
        mon = monitor(clock=clock, base=60, ceiling=900)
        seen = []
        for _ in range(6):
            token = mon.begin_check()
            mon.stat_returned()
            mon.finish_check(token, False, 'down')
            seen.append(mon.interval)
            clock.now += mon.interval
        assert seen == [120, 240, 480, 900, 900, 900]
        token = mon.begin_check()
        mon.stat_returned()
        mon.finish_check(token, True, '')
        assert mon.interval == 60

    def test_positive_result_older_than_twice_the_interval_reads_unreachable(self):
        clock = Clock()
        mon = monitor(clock=clock, base=60)
        token = mon.begin_check()
        mon.stat_returned()
        mon.finish_check(token, True, '')
        clock.now += 120
        assert mon.state().reachable is True
        clock.now += 1
        current = mon.state()
        assert current.reachable is False and 'stale' in current.last_error


class TestForcedUnreachable:

    def test_mark_unreachable_is_immediate_and_reschedules_at_base(self):
        clock = Clock()
        mon = monitor(clock=clock)
        token = mon.begin_check()
        mon.stat_returned()
        mon.finish_check(token, True, '')
        mon.mark_unreachable('copy stalled')
        assert mon.state().reachable is False
        assert mon.state().last_error == 'copy stalled'
        assert mon.begin_check() is None
        clock.now += 60
        assert mon.begin_check() is not None

    def test_success_from_a_check_started_before_the_mark_is_discarded(self):
        clock = Clock()
        mon = monitor(clock=clock)
        token = mon.begin_check()
        mon.mark_unreachable('copy stalled')
        mon.stat_returned()
        assert mon.finish_check(token, True, '') is False
        assert mon.state().reachable is False
        clock.now += 60
        token = mon.begin_check()
        mon.stat_returned()
        assert mon.finish_check(token, True, '') is True
        assert mon.state().reachable is True


class TestThread:

    def test_thread_checks_and_stops(self):
        done = threading.Event()

        def counting_stat(path):
            done.set()
            return dir_stat(path)

        mon = unreal_share.ShareMonitor(ROOT, 60, 900, 5, stat_fn=counting_stat)
        mon.start()
        try:
            assert done.wait(5)
            for _ in range(200):
                if mon.state().reachable:
                    break
                threading.Event().wait(0.01)
            assert mon.state().reachable is True
        finally:
            mon.stop(timeout=5)
        assert not mon._thread.is_alive()


class TestNoShareFromTheManager:

    def test_module_has_no_response_hook(self):
        assert not hasattr(unreal_share, 'update_from_response')

    def test_stat_targets_only_the_configured_root(self):
        seen = []

        def recording(path):
            seen.append(path)
            return dir_stat(path)

        mon = monitor(stat_fn=recording)
        token = mon.begin_check()
        mon.finish_check(token, *mon.check_now())
        assert seen == [ROOT.canonical]
        assert os.fspath(seen[0]).startswith('\\\\nas.lan\\')
