# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Recovery step 1 fails closed when the orphan sweep raises (FR-PA-18, FR-UE-23; chunk 7a review R2-L1)."""
import pytest

from sethlans_worker_agent.project_archive import owner_lock, recovery
from sethlans_worker_agent.project_archive.cache import CacheLimits
from sethlans_worker_agent.project_archive.layout import CacheLayout
from sethlans_worker_agent.unreal_executor.cache_dir import PreparedCacheDir

from . import _helpers as h

LIMITS = CacheLimits(cache_max_bytes=100 * h.GIB, min_free_bytes=h.GIB)


@pytest.fixture()
def root(tmp_path):
    path = h.new_cache_root(tmp_path)
    assert owner_lock.acquire_for_process(path)
    yield path
    owner_lock.release_for_process()


def test_a_raising_sweep_stops_recovery_before_any_deletion(root):
    CacheLayout(root).ensure_children()
    leftover = root / 'w' / 'stuff' / 'Demo' / 'f'
    leftover.parent.mkdir(parents=True)
    leftover.write_bytes(b'x')

    def sweep():
        raise TypeError("sweep() missing 2 required positional arguments")
    result = recovery.recover(PreparedCacheDir(root, True), LIMITS, keep_job_logs=10, disk_usage=h.big_disk(),
                              sweep=sweep)
    assert result.cache is None
    assert result.reason == f"{recovery.SWEEP_REASON} (TypeError)"
    assert leftover.exists()


def test_a_successful_closure_sweep_lets_recovery_continue(root):
    CacheLayout(root).ensure_children()
    calls = []
    result = recovery.recover(PreparedCacheDir(root, True), LIMITS, keep_job_logs=10, disk_usage=h.big_disk(),
                              sweep=lambda: calls.append('swept'))
    assert result.reason == '' and result.cache is not None and calls == ['swept']
