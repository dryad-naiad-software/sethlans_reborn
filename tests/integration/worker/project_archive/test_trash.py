# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Entry removal and ledger review fixes (chunk-6 reviews Conc C1, C3, C6,
C8): pending moves never delete a live entry; deletes never raise;
survivors quarantine; concurrent populates count against CACHE_MAX.
"""
import pytest

from sethlans_worker_agent.project_archive import eviction, fs_safe, populate
from sethlans_worker_agent.project_archive import trash as trash_module
from sethlans_worker_agent.project_archive.abort import Abort
from sethlans_worker_agent.project_archive.cache import CacheLimits
from sethlans_worker_agent.project_archive.errors import InsufficientDiskError

from . import _helpers as h


def _obtain(cache, archive, pin):
    return populate.obtain_entry(cache, pin, h.local_fetch(cache, archive, pin), Abort(), 10 * h.GIB)


def test_failed_move_never_deletes_the_republished_entry(tmp_path, monkeypatch):
    archive = tmp_path / 'a.zip'
    pin = h.make_zip(archive)
    cache = h.make_cache(tmp_path)
    entry = _obtain(cache, archive, pin)
    cache.release(pin)
    real_move = fs_safe.move_aside
    stuck = {'on': True}
    monkeypatch.setattr(trash_module.fs_safe, 'move_aside',
                        lambda path, trash: None if stuck['on'] and path == entry.entry_dir else real_move(path, trash))
    cache.quarantine(pin, 'test')  # refcount 0: dispose, but the move fails
    assert entry.entry_dir.exists() and cache.state(pin) is None
    with cache.lock:
        assert cache.trash.pending_keys_locked() == {pin[:16]}
    stuck['on'] = False
    again = _obtain(cache, archive, pin)  # re-publish moves the old directory aside itself
    with cache.lock:
        assert cache.trash.pending_keys_locked() == set()
    eviction.reserve(cache, 1).release()  # retries pending work
    assert again.entry_dir.exists() and (again.tree / 'Demo' / 'Demo.uproject').exists()
    assert cache.state(pin) == 'ready' and cache.ledger.pending_trash == 0
    cache.release(pin)


def test_pending_move_is_dropped_when_the_key_is_registered_again(tmp_path, monkeypatch):
    archive = tmp_path / 'a.zip'
    pin = h.make_zip(archive)
    cache = h.make_cache(tmp_path)
    entry = _obtain(cache, archive, pin)
    cache.release(pin)
    monkeypatch.setattr(trash_module.fs_safe, 'move_aside', lambda path, trash: None)
    cache.quarantine(pin, 'test')
    with cache.lock:  # the same key published by some other path
        cache.register_locked(pin, 1, '', acquired=False, digest='x')
    cache.retry_pending_trash()
    with cache.lock:
        assert cache.trash.pending_keys_locked() == set()
    assert entry.entry_dir.exists()


def test_pending_move_succeeds_on_retry(tmp_path, monkeypatch):
    archive = tmp_path / 'a.zip'
    pin = h.make_zip(archive)
    cache = h.make_cache(tmp_path)
    entry = _obtain(cache, archive, pin)
    cache.release(pin)
    real_move = fs_safe.move_aside
    stuck = {'on': True}
    monkeypatch.setattr(trash_module.fs_safe, 'move_aside',
                        lambda path, trash: None if stuck['on'] else real_move(path, trash))
    cache.quarantine(pin, 'test')
    stuck['on'] = False
    cache.retry_pending_trash()
    assert not entry.entry_dir.exists() and cache.ledger.pending_trash == 0
    assert list(cache.layout.trash.iterdir()) == []


def test_remove_tree_never_raises(tmp_path, monkeypatch):
    def denied(path):
        raise PermissionError('denied')

    monkeypatch.setattr(fs_safe, 'lstat', denied)
    assert fs_safe.remove_tree(tmp_path / 'x') is False


def test_survivors_quarantine_the_entry(tmp_path):
    from sethlans_worker_agent.project_archive import workspace
    archive = tmp_path / 'share' / 'incoming' / 'Demo.zip'
    archive.parent.mkdir(parents=True)
    pin = h.make_zip(archive)
    cache = h.make_cache(tmp_path)
    request = workspace.ArchiveRequest(job_id=1, attempt='abcdef01', share_root=h.SHARE_ROOT,
                                       archive_relpath='incoming/Demo.zip', archive_sha256=pin,
                                       uproject_relpath='Demo/Demo.uproject')
    ws = workspace.prepare_workspace(cache, request, limits=h.limits(), reachability=h.AlwaysReachable(),
                                     resolve=h.local_resolver(tmp_path / 'share'), share_validator=lambda p: p)
    report = ws.finish(survivors=True)
    assert report.summary == 'pristine_integrity=skipped(survivors)'
    assert cache.state(pin) is None and not cache.entry_dir(pin).exists()


def test_concurrent_populates_count_against_cache_max(tmp_path):
    cache = h.make_cache(tmp_path)
    cache.limits = CacheLimits(cache_max_bytes=1000, min_free_bytes=0)
    first = eviction.reserve(cache, 600, populate_bytes=600)
    with pytest.raises(InsufficientDiskError):
        eviction.reserve(cache, 600, populate_bytes=600)
    first.release()
    assert cache.ledger.in_flight_populate == 0
    eviction.reserve(cache, 600, populate_bytes=600).release()
