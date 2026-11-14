# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Share reachability of archive and overlay paths (Unreal foundation
FR-PA-3, Dom N-16, Sec N5).
"""
import os
import stat
import threading

import pytest

from sethlans_worker_agent import unreal_share
from sethlans_worker_agent.project_archive import reachability
from shared.unreal.unc import validate_unc_dir, validate_unc_path

ROOT = validate_unc_dir('\\\\nas.lan\\sethlans')
REACHABLE = unreal_share.ShareState(ROOT, True, 0.0, '')
UNREACHABLE = unreal_share.ShareState(ROOT, False, 0.0, 'down')


def _st(mode):
    return os.stat_result((mode, 0, 0, 1, 0, 0, 10, 0, 0, 0))


def _resolver(root, relpath, kind):
    if '..' in relpath:
        raise ValueError("path resolves outside the Sethlans share")
    return validate_unc_path(root.canonical + '\\' + relpath.replace('/', '\\'))


class FakeStat:
    def __init__(self, modes=None, block=None):
        self.calls = []
        self.modes = modes or {}
        self.block = block

    def __call__(self, path, follow_symlinks=True):
        assert follow_symlinks is False
        self.calls.append(path)
        if self.block is not None:
            self.block.wait(5)
        mode = self.modes.get(path, stat.S_IFREG | 0o644)
        if mode is None:
            raise FileNotFoundError(path)
        return _st(mode)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _reach(fake, state=REACHABLE, clock=None, timeout=2.0):
    return reachability.Reachability(timeout, state_fn=lambda: state, resolver=_resolver, stat_fn=fake,
                                     clock=clock or Clock())


HOST = '\\\\nas.lan\\sethlans'
ARCHIVE = '\\\\nas.lan\\sethlans\\incoming\\Demo.zip'


def test_reachable_file():
    fake = FakeStat({HOST: stat.S_IFDIR | 0o755})
    assert _reach(fake).check(ROOT, 'incoming/Demo.zip', 'file', job_id=1) is True
    assert fake.calls == [HOST, ARCHIVE]


def test_unreachable_share_state_makes_no_call():
    fake = FakeStat()
    assert _reach(fake, state=UNREACHABLE).check(ROOT, 'incoming/Demo.zip', 'file') is False
    assert fake.calls == []


def test_path_outside_the_root_makes_no_call():
    fake = FakeStat()
    assert _reach(fake).check(ROOT, '../escape.zip', 'file') is False
    assert fake.calls == []


@pytest.mark.parametrize('mode,kind,expected', [
    (stat.S_IFLNK | 0o777, 'file', False), (stat.S_IFDIR | 0o755, 'file', False),
    (stat.S_IFREG | 0o644, 'dir', False), (stat.S_IFDIR | 0o755, 'dir', True), (None, 'file', False),
])
def test_kind_and_reparse_rules(mode, kind, expected):
    fake = FakeStat({HOST: stat.S_IFDIR | 0o755, ARCHIVE: mode})
    assert _reach(fake).check(ROOT, 'incoming/Demo.zip', kind) is expected


def test_host_result_is_cached_and_positive_paths_are_not():
    clock = Clock()
    fake = FakeStat({HOST: stat.S_IFDIR | 0o755})
    reach = _reach(fake, clock=clock)
    assert reach.check(ROOT, 'incoming/Demo.zip', 'file')
    assert reach.check(ROOT, 'incoming/Demo.zip', 'file')
    assert fake.calls == [HOST, ARCHIVE, ARCHIVE]
    clock.now += reachability.HOST_POSITIVE_TTL + 1
    reach.check(ROOT, 'incoming/Demo.zip', 'file')
    assert fake.calls.count(HOST) == 2


def test_negative_path_result_is_cached_per_job_for_sixty_seconds():
    clock = Clock()
    fake = FakeStat({HOST: stat.S_IFDIR | 0o755, ARCHIVE: None})
    reach = _reach(fake, clock=clock)
    assert reach.check(ROOT, 'incoming/Demo.zip', 'file', job_id=7) is False
    assert reach.check(ROOT, 'incoming/Demo.zip', 'file', job_id=7) is False
    assert fake.calls.count(ARCHIVE) == 1
    assert reach.check(ROOT, 'incoming/Demo.zip', 'file', job_id=8) is False
    assert fake.calls.count(ARCHIVE) == 2
    clock.now += reachability.PATH_NEGATIVE_TTL + 1
    reach.check(ROOT, 'incoming/Demo.zip', 'file', job_id=7)
    assert fake.calls.count(ARCHIVE) == 3


def test_negative_host_is_cached():
    fake = FakeStat({HOST: None})
    reach = _reach(fake)
    assert reach.check(ROOT, 'incoming/Demo.zip', 'file') is False
    assert reach.check(ROOT, 'other.zip', 'file') is False
    assert fake.calls == [HOST]


def test_exhausted_budget_returns_pending_and_caches_later():
    block = threading.Event()
    fake = FakeStat({HOST: stat.S_IFDIR | 0o755}, block=block)
    reach = reachability.Reachability(5.0, state_fn=lambda: REACHABLE, resolver=_resolver, stat_fn=fake)
    budget = reachability.ProbeBudget(0.05)
    assert reach.check(ROOT, 'incoming/Demo.zip', 'file', pool='poll', budget=budget) is None
    block.set()
    for _ in range(100):
        if reach._cached_host(('nas.lan', 'sethlans')) is not None:
            break
        threading.Event().wait(0.02)
    assert reach._cached_host(('nas.lan', 'sethlans')) is True


def test_timeout_is_negative():
    block = threading.Event()
    fake = FakeStat({HOST: stat.S_IFDIR | 0o755}, block=block)
    reach = reachability.Reachability(0.05, state_fn=lambda: REACHABLE, resolver=_resolver, stat_fn=fake)
    try:
        assert reach.check(ROOT, 'incoming/Demo.zip', 'file') is False
    finally:
        block.set()


def test_default_resolver_contains_the_result():
    pytest.importorskip('shared.unreal.share_paths')
    resolved = reachability.resolve_share_path(ROOT, 'incoming/Demo.zip', 'file')
    assert resolved.is_within(ROOT)
    with pytest.raises(ValueError):
        reachability.resolve_share_path(ROOT, '../Demo.zip', 'file')


def test_pending_host_probe_is_reused():
    """Conc C5: at most one host probe per (host, share) in flight."""
    block = threading.Event()
    fake = FakeStat({HOST: stat.S_IFDIR | 0o755}, block=block)
    reach = reachability.Reachability(5.0, state_fn=lambda: REACHABLE, resolver=_resolver, stat_fn=fake)
    try:
        for _ in range(3):
            budget = reachability.ProbeBudget(0.05)
            assert reach.check(ROOT, 'incoming/Demo.zip', 'file', pool='poll', budget=budget) is None
        assert fake.calls == [HOST]
    finally:
        block.set()


def test_job_pool_never_waits_on_a_poll_pool_host_probe():
    """FR-PA-3: a stuck poll-pool host probe is not shared with the job pool."""
    block = threading.Event()
    fake = FakeStat({HOST: stat.S_IFDIR | 0o755}, block=block)
    reach = reachability.Reachability(5.0, state_fn=lambda: REACHABLE, resolver=_resolver, stat_fn=fake)
    try:
        key = ('nas.lan', 'sethlans')
        poll_future = reach._host_future('poll', key, HOST)
        job_future = reach._host_future('job', key, HOST)
        assert job_future is not poll_future
        assert reach._host_future('poll', key, HOST) is poll_future
    finally:
        block.set()


def test_an_older_probe_never_overwrites_a_newer_result():
    clock = Clock()
    fake = FakeStat({HOST: None})
    reach = _reach(fake, clock=clock)
    key = ('nas.lan', 'sethlans')
    with reach._lock:
        reach._hosts[key] = (True, clock.now + 10, 500.0)
    assert reach._host_probe(key, HOST, started=400.0) is False  # started before the cached probe
    assert reach._cached_host(key) is True
    reach._host_probe(key, HOST, started=600.0)
    assert reach._cached_host(key) is False


class _EarlyWakeBudget(reachability.ProbeBudget):
    """A budget that still shows a sliver left after the wait timed out (a timer tick early, #231)."""

    def remaining(self) -> float:
        return 0.02


def test_budget_capped_timeout_is_pending_even_if_the_wait_wakes_early():
    block = threading.Event()
    fake = FakeStat({HOST: stat.S_IFDIR | 0o755}, block=block)
    reach = reachability.Reachability(5.0, state_fn=lambda: REACHABLE, resolver=_resolver, stat_fn=fake)
    try:
        budget = _EarlyWakeBudget(0.02)
        assert reach.check(ROOT, 'incoming/Demo.zip', 'file', pool='poll', budget=budget) is None
    finally:
        block.set()


def test_full_probe_timeout_is_negative_even_with_a_budget():
    block = threading.Event()
    fake = FakeStat({HOST: stat.S_IFDIR | 0o755}, block=block)
    reach = reachability.Reachability(0.05, state_fn=lambda: REACHABLE, resolver=_resolver, stat_fn=fake)
    try:
        assert reach.check(ROOT, 'incoming/Demo.zip', 'file', pool='poll',
                           budget=reachability.ProbeBudget(10.0)) is False
    finally:
        block.set()
