# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Byte ledger and eviction (Unreal foundation FR-PA-11, FR-T-6; Conc
R2-L4 stage-B double count).
"""
import threading

import pytest

from sethlans_worker_agent.project_archive import cache as cache_module
from sethlans_worker_agent.project_archive import eviction, fs_safe, populate
from sethlans_worker_agent.project_archive import trash as trash_module
from sethlans_worker_agent.project_archive.abort import Abort
from sethlans_worker_agent.project_archive.errors import InsufficientDiskError

from . import _helpers as h

MIN_FREE = 1000


class LedgerDisk:
    """``free`` = base + bytes of entries already deleted (like a real disk)."""

    def __init__(self, base):
        self.base = base
        self.cache = None
        self.sizes = {}

    def __call__(self, _path):
        freed = sum(size for sha, size in self.sizes.items() if not self.cache.entry_dir(sha).exists())
        return h.DiskUsage(10 ** 12, 0, self.base + freed)


def _populate(tmp_path, cache, name, payload_size):
    archive = tmp_path / f'{name}.zip'
    files = dict(h.PROJECT_FILES)
    files['Demo/Content/Payload.bin'] = (name.encode() * payload_size)[:payload_size]
    pin = h.make_zip(archive, files=files)
    entry = populate.obtain_entry(cache, pin, h.local_fetch(cache, archive, pin), Abort(), 10 * h.GIB)
    cache.release(pin)
    return pin, entry.record.total_bytes


def test_two_concurrent_reservations_cannot_exceed_min_free(tmp_path):
    cache = h.make_cache(tmp_path, min_free=MIN_FREE, disk_usage=lambda p: h.DiskUsage(10 ** 6, 0, MIN_FREE + 100))
    barrier = threading.Barrier(2)
    outcomes = []

    def reserve():
        barrier.wait()
        try:
            outcomes.append(eviction.reserve(cache, 60))
        except InsufficientDiskError as exc:
            outcomes.append(exc)

    threads = [threading.Thread(target=reserve, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    granted = [o for o in outcomes if not isinstance(o, Exception)]
    assert len(granted) == 1 and len(outcomes) == 2
    assert cache.ledger.in_flight_reserved == 60
    granted[0].release()
    assert cache.ledger.in_flight_reserved == 0


def test_eviction_stops_as_soon_as_the_reservation_fits(tmp_path):
    disk = LedgerDisk(base=500 * h.GIB)
    cache = h.make_cache(tmp_path, min_free=MIN_FREE, disk_usage=disk)
    disk.cache = cache
    clock = iter(range(1, 100))
    cache._clock = lambda: float(next(clock))
    pins = []
    for name in ('old', 'mid', 'new'):
        pin, size = _populate(tmp_path, cache, name, 5000)
        disk.sizes[pin] = size
        pins.append(pin)
    disk.base = MIN_FREE
    first_size = disk.sizes[pins[0]]
    reservation = eviction.reserve(cache, first_size)
    assert not cache.entry_dir(pins[0]).exists()
    assert cache.entry_dir(pins[1]).exists() and cache.entry_dir(pins[2]).exists()
    assert cache.state(pins[1]) == 'ready' and cache.state(pins[2]) == 'ready'
    reservation.release()


def test_nothing_is_evicted_when_even_all_idle_entries_are_not_enough(tmp_path):
    disk = LedgerDisk(base=500 * h.GIB)
    cache = h.make_cache(tmp_path, min_free=MIN_FREE, disk_usage=disk)
    disk.cache = cache
    pin, size = _populate(tmp_path, cache, 'only', 5000)
    disk.sizes[pin] = size
    disk.base = MIN_FREE
    with pytest.raises(InsufficientDiskError, match=r'^Insufficient local disk for Unreal project cache: need '):
        eviction.reserve(cache, size * 10)
    assert cache.state(pin) == 'ready'


def test_cache_max_evicts_for_a_populate(tmp_path):
    cache = h.make_cache(tmp_path, min_free=MIN_FREE)
    pin_a, size_a = _populate(tmp_path, cache, 'a', 5000)
    cache.limits = cache_module.CacheLimits(cache_max_bytes=size_a + 100, min_free_bytes=MIN_FREE)
    pin_b, _size_b = _populate(tmp_path, cache, 'b', 5000)
    assert cache.state(pin_a) is None and cache.state(pin_b) == 'ready'
    assert cache.ledger.pristine_total == _size_b


def test_stage_b_does_not_count_the_archive_twice(tmp_path):
    """free leaves room for exactly the declared bytes; the copy's reservation is already spent."""
    archive = tmp_path / 'demo.zip'
    pin = h.make_zip(archive)
    declared = sum(len(v) for v in h.PROJECT_FILES.values())
    assert archive.stat().st_size < declared
    cache = h.make_cache(tmp_path, min_free=MIN_FREE,
                         disk_usage=lambda p: h.DiskUsage(10 ** 9, 0, MIN_FREE + declared))
    entry = populate.obtain_entry(cache, pin, h.local_fetch(cache, archive, pin), Abort(), 10 * h.GIB)
    assert entry.hit is False and cache.ledger.in_flight_reserved == 0
    cache.release(pin)


def test_failed_trash_delete_is_kept_pending_and_retried(tmp_path, monkeypatch):
    disk = LedgerDisk(base=500 * h.GIB)
    cache = h.make_cache(tmp_path, min_free=MIN_FREE, disk_usage=disk)
    disk.cache = cache
    pin, size = _populate(tmp_path, cache, 'a', 5000)
    real_remove = fs_safe.remove_tree
    failures = []

    def flaky_remove(path):
        if not failures:
            failures.append(path)
            return False
        return real_remove(path)

    monkeypatch.setattr(trash_module.fs_safe, 'remove_tree', flaky_remove)
    cache.quarantine(pin, 'test')
    assert cache.ledger.pending_trash == size and cache.ledger.pristine_total == 0
    eviction.reserve(cache, 1).release()  # a later reservation retries the delete
    assert cache.ledger.pending_trash == 0
    assert list(cache.layout.trash.iterdir()) == []
