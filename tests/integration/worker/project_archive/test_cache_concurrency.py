# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Cache concurrency (Unreal foundation FR-PA-10, FR-T-6 "Cache concurrency
and eviction", AC-27; Conc R2-L5).
"""
import shutil
import threading
import time

import pytest

from sethlans_worker_agent.project_archive import eviction, populate, safe_extract
from sethlans_worker_agent.project_archive.abort import Abort
from sethlans_worker_agent.project_archive.errors import (
    AbortedError, ArchivePinMismatchError, InsufficientDiskError,
)

from . import _helpers as h


def _run_threads(*targets):
    results = [None] * len(targets)

    def wrap(index, fn):
        try:
            results[index] = ('ok', fn())
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            results[index] = ('error', exc)

    threads = [threading.Thread(target=wrap, args=(i, fn), daemon=True) for i, fn in enumerate(targets)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert not any(thread.is_alive() for thread in threads)
    return results


def test_same_pin_under_two_paths_copies_and_extracts_once(tmp_path, monkeypatch):
    first = tmp_path / 'share' / 'a' / 'demo.zip'
    first.parent.mkdir(parents=True)
    pin = h.make_zip(first)
    second = tmp_path / 'share' / 'b' / 'same.zip'
    second.parent.mkdir(parents=True)
    shutil.copyfile(first, second)
    cache = h.make_cache(tmp_path)
    copies, extracts = [], []
    original = safe_extract.OpenedArchive.extract
    monkeypatch.setattr(safe_extract.OpenedArchive, 'extract',
                        lambda self, *a, **k: extracts.append(1) or original(self, *a, **k))
    barrier = threading.Barrier(2)

    def job(path):
        barrier.wait()
        return populate.obtain_entry(cache, pin, h.local_fetch(cache, path, pin, counter=copies), Abort(),
                                     10 * h.GIB)

    results = _run_threads(lambda: job(first), lambda: job(second))
    assert [status for status, _ in results] == ['ok', 'ok']
    assert len(copies) == 1 and len(extracts) == 1
    assert sorted(entry.hit for _, entry in results) == [False, True]
    assert cache.refcount(pin) == 2


def test_wrong_bytes_fail_under_the_lock_and_next_waiter_proceeds(tmp_path):
    right = tmp_path / 'right.zip'
    pin = h.make_zip(right)
    wrong = tmp_path / 'wrong.zip'
    h.make_zip(wrong, files={'Demo/Demo.uproject': b'other'})
    cache = h.make_cache(tmp_path)
    in_fetch, waiter_started, copies = threading.Event(), threading.Event(), []
    wrong_fetch = h.local_fetch(cache, wrong, pin, counter=copies)

    def slow_wrong_fetch(abort):
        in_fetch.set()
        waiter_started.wait(5)
        time.sleep(0.2)  # the second job is now waiting on the populate lock
        return wrong_fetch(abort)

    def first_job():
        return populate.obtain_entry(cache, pin, slow_wrong_fetch, Abort(), 10 * h.GIB)

    def second_job():
        in_fetch.wait(5)
        waiter_started.set()
        return populate.obtain_entry(cache, pin, h.local_fetch(cache, right, pin, counter=copies), Abort(),
                                     10 * h.GIB)

    (s1, r1), (s2, r2) = _run_threads(first_job, second_job)
    assert s1 == 'error' and isinstance(r1, ArchivePinMismatchError)
    assert s2 == 'ok' and r2.hit is False and r2.sha256 == pin
    assert copies == [wrong, right]
    cache.release(pin)


def test_populate_lock_waiter_observes_cancel_within_two_seconds(tmp_path):
    cache = h.make_cache(tmp_path)
    holding, release = threading.Event(), threading.Event()

    def holder():
        with cache.populate_locks.hold('a' * 64):
            holding.set()
            release.wait(10)

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    try:
        assert holding.wait(5)
        cancel = threading.Event()
        timer = threading.Timer(0.1, cancel.set)
        timer.start()
        started = time.monotonic()
        with pytest.raises(AbortedError):
            populate.obtain_entry(cache, 'a' * 64, lambda abort: None, Abort(cancel=cancel), 10 * h.GIB)
        assert time.monotonic() - started < 2.0
        timer.join()
    finally:
        release.set()
        thread.join(10)
    assert cache.populate_locks.known() == 0


def test_one_populate_lock_per_thread(tmp_path):
    cache = h.make_cache(tmp_path)
    with cache.populate_locks.hold('a' * 64):
        with pytest.raises(RuntimeError, match='only one populate lock'):
            with cache.populate_locks.hold('b' * 64):
                pass


def _ready_entry(tmp_path, cache, name='a.zip', extra=b''):
    archive = tmp_path / name
    files = dict(h.PROJECT_FILES)
    files['Demo/Content/Unique.uasset'] = name.encode() + extra
    pin = h.make_zip(archive, files=files)
    populate.obtain_entry(cache, pin, h.local_fetch(cache, archive, pin), Abort(), 10 * h.GIB)
    cache.release(pin)
    return pin


def test_entry_marked_evicting_cannot_be_acquired(tmp_path):
    cache = h.make_cache(tmp_path)
    pin = _ready_entry(tmp_path, cache)
    with cache.lock:
        victims = cache.lru_candidates_locked()
        removals = cache.begin_eviction_locked(victims)
    assert cache.acquire(pin) is False
    cache.dispose(removals)
    assert not cache.entry_dir(pin).exists()
    assert cache.ledger.pristine_total == 0 and cache.ledger.pending_trash == 0


class SwitchableDisk:
    """``free`` is large while entries are populated, then whatever the test sets."""

    def __init__(self):
        self.free = 500 * h.GIB
        self.entry_dir = None
        self.after_eviction = None

    def __call__(self, _path):
        free = self.free
        if self.after_eviction is not None and self.entry_dir is not None and not self.entry_dir.exists():
            free = self.after_eviction
        return h.DiskUsage(10 * self.free + 1, 0, free)


def test_acquired_entry_is_never_selected(tmp_path):
    disk = SwitchableDisk()
    cache = h.make_cache(tmp_path, min_free=0, disk_usage=disk)
    pin = _ready_entry(tmp_path, cache)
    disk.free = 0
    assert cache.acquire(pin)
    with pytest.raises(InsufficientDiskError):
        eviction.reserve(cache, 50)
    assert cache.state(pin) == 'ready' and cache.entry_dir(pin).exists()
    cache.release(pin)


def test_evict_vs_acquire_race(tmp_path):
    for round_index in range(10):
        root = tmp_path / f'r{round_index}'
        root.mkdir()
        disk = SwitchableDisk()
        cache = h.make_cache(root, min_free=0, disk_usage=disk)
        pin = _ready_entry(root, cache)
        disk.entry_dir, disk.free, disk.after_eviction = cache.entry_dir(pin), 0, 10 ** 6
        barrier = threading.Barrier(2)

        def acquire():
            barrier.wait()
            return cache.acquire(pin)

        def evict():
            barrier.wait()
            return eviction.reserve(cache, 1)

        (sa, acquired), (se, outcome) = _run_threads(acquire, evict)
        if acquired:
            assert se == 'error' and isinstance(outcome, InsufficientDiskError)
            assert cache.state(pin) == 'ready' and cache.entry_dir(pin).exists()
            cache.release(pin)
        else:
            assert se == 'ok' and cache.state(pin) is None and not cache.entry_dir(pin).exists()
            outcome.release()


def test_quarantine_in_use_defers_the_rename(tmp_path):
    cache = h.make_cache(tmp_path)
    pin = _ready_entry(tmp_path, cache)
    assert cache.acquire(pin)
    cache.quarantine(pin, 'test')
    assert cache.state(pin) == 'quarantined' and cache.entry_dir(pin).exists()
    assert cache.acquire(pin) is False
    cache.release(pin)
    assert cache.state(pin) is None and not cache.entry_dir(pin).exists()
    assert list(cache.layout.trash.iterdir()) == []
