# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Startup recovery (Unreal foundation FR-PA-18, FR-WC-4 untrusted content,
Sec N2, FR-T-6 "Pin and publish" adoption cases, AC-24).
"""
import json
import os
import sys
import time

import pytest

from sethlans_worker_agent.project_archive import fs_safe, owner_lock, populate, recovery
from sethlans_worker_agent.project_archive.abort import Abort
from sethlans_worker_agent.project_archive.cache import CacheLimits
from sethlans_worker_agent.project_archive.layout import CacheLayout
from sethlans_worker_agent.unreal_executor.cache_dir import ACL_MARKER, PreparedCacheDir
from shared import owner_only_acl

from . import _helpers as h

LIMITS = CacheLimits(cache_max_bytes=100 * h.GIB, min_free_bytes=h.GIB)


@pytest.fixture()
def root(tmp_path):
    path = h.new_cache_root(tmp_path)
    assert owner_lock.acquire_for_process(path)
    yield path
    owner_lock.release_for_process()


def _recover(root, trusted=True, **kwargs):
    kwargs.setdefault('keep_job_logs', 10)
    return recovery.recover(PreparedCacheDir(root, trusted), LIMITS, disk_usage=h.big_disk(), **kwargs)


def _touch(path, data=b'x'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_without_the_owner_lock_nothing_runs(tmp_path):
    path = h.new_cache_root(tmp_path)
    _touch(path / 'w' / 'keep' / 'file.txt')
    result = recovery.recover(PreparedCacheDir(path, True), LIMITS, keep_job_logs=10)
    assert result.cache is None and result.reason == owner_lock.OWNER_LOCK_REASON
    assert (path / 'w' / 'keep' / 'file.txt').exists()


def test_trusted_start_clears_transient_directories(root):
    CacheLayout(root).ensure_children()
    _touch(root / 'downloads' / 'abc.zip.part')
    _touch(root / 'p' / '0123456789abcdef.tmp-1234' / 'tree' / 'f')
    _touch(root / 'trash' / 'old' / 'f')
    _touch(root / 'w' / 'stuff' / 'Demo' / 'f')
    _touch(root / 'o' / '42-abcdef01' / 'f')
    _touch(root / 'index.json.tmp-dead')
    for index in range(3):
        job = root / 'j' / f'{index}-abcdef0{index}'
        _touch(job / 'output' / 'x.png')
        _touch(job / 'logs' / 'render.1.log')
        os.utime(job, ns=(time.time_ns(), time.time_ns() + index * 10 ** 9))
    sweeps = []
    result = _recover(root, keep_job_logs=2, sweep=lambda: sweeps.append((root / 'w' / 'stuff').exists()))
    assert result.reason == '' and result.cache is not None
    assert sweeps == [True]  # the sweep ran before any deletion
    for name in ('downloads', 'trash', 'w', 'o', 'p'):
        assert list((root / name).iterdir()) == [], name
    assert not (root / 'index.json.tmp-dead').exists()
    jobs = sorted(p.name for p in (root / 'j').iterdir())
    assert jobs == ['1-abcdef01', '2-abcdef02']
    assert all(not (root / 'j' / name / 'output').exists() for name in jobs)
    assert all((root / 'j' / name / 'logs' / 'render.1.log').exists() for name in jobs)


def _dir_link(link, target):
    if sys.platform == 'win32':
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_untrusted_content_is_moved_to_trash(root, tmp_path):
    profile = tmp_path / 'profile'
    _touch(profile / 'thesis.docx', b'precious')
    _touch(root / 'w' / 'x' / 'f')
    _touch(root / 'j' / '1-abcdef01' / 'logs' / 'l.log')
    _touch(root / 'downloads' / 'a.part')
    _touch(root / 'index.json', b'{"schema": 1, "entries": {}}')
    _touch(root / 'zen' / 'data' / 'cas')
    os.mkdir(root / 'o')
    _dir_link(root / 'p', profile)
    result = _recover(root, trusted=False)
    assert result.reason == '' and result.prepared.content_trusted is True
    assert (root / ACL_MARKER).exists()
    assert sorted(p.name for p in root.iterdir()) == sorted(
        ['.owner.lock', ACL_MARKER, 'downloads', 'index.json', 'j', 'o', 'p', 'trash', 'w'])
    assert json.loads((root / 'index.json').read_text(encoding='utf-8'))['entries'] == {}
    for name in ('downloads', 'j', 'o', 'p', 'trash', 'w'):
        assert list((root / name).iterdir()) == [], name
    assert (profile / 'thesis.docx').read_bytes() == b'precious'
    assert result.cache.ready_shas() == []


def test_job_dir_link_is_removed_without_touching_its_target(root, tmp_path):
    CacheLayout(root).ensure_children()
    profile = tmp_path / 'profile'
    _touch(profile / 'output' / 'keep.png', b'keep')
    _dir_link(root / 'j' / '9-abcdef09', profile)
    result = _recover(root)
    assert result.reason == ''
    assert not os.path.lexists(root / 'j' / '9-abcdef09')
    assert (profile / 'output' / 'keep.png').read_bytes() == b'keep'


def test_stuck_working_directory_disables_unreal(root, monkeypatch):
    CacheLayout(root).ensure_children()
    _touch(root / 'w' / 'held' / 'f')
    real = fs_safe.remove_tree
    monkeypatch.setattr(recovery.fs_safe, 'remove_tree',
                        lambda path: False if 'held' in str(path) else real(path))
    result = _recover(root)
    assert result.cache is None and result.reason == recovery.WORK_REASON


def test_foreign_fixed_child_is_replaced(root, monkeypatch):
    CacheLayout(root).ensure_children()
    _touch(root / 'o' / 'planted.txt')
    real = owner_only_acl.is_owned_by_current_user
    monkeypatch.setattr(owner_only_acl, 'is_owned_by_current_user',
                        lambda path: False if os.path.basename(os.fspath(path)) == 'o' and
                        (root / 'o' / 'planted.txt').exists() else real(path))
    result = _recover(root)
    assert result.reason == ''
    assert (root / 'o').is_dir() and list((root / 'o').iterdir()) == []
    assert not any(name.startswith('.aside-') for name in os.listdir(root))


def _populated(tmp_path, root, name='a'):
    result = _recover(root)
    archive = tmp_path / f'{name}.zip'
    files = dict(h.PROJECT_FILES)
    files['Demo/Content/Unique.uasset'] = name.encode()
    pin = h.make_zip(archive, files=files)
    entry = populate.obtain_entry(result.cache, pin, h.local_fetch(result.cache, archive, pin), Abort(), 10 * h.GIB)
    result.cache.release(pin)
    return pin, entry


def test_indexed_entries_survive_a_restart(root, tmp_path):
    pin, entry = _populated(tmp_path, root)
    result = _recover(root)
    assert result.cache.ready_shas() == [pin]
    assert result.cache.ledger.pristine_total == entry.record.total_bytes


def test_index_key_without_complete_json_is_dropped(root, tmp_path):
    pin, entry = _populated(tmp_path, root)
    (entry.entry_dir / '.complete.json').unlink()
    result = _recover(root)
    assert result.cache.ready_shas() == [] and not entry.entry_dir.exists()


def test_unindexed_entry_is_never_adopted(root, tmp_path):
    """Sec S1d: even a genuine, self-consistent unindexed entry goes to trash."""
    pin, entry = _populated(tmp_path, root)
    (root / 'index.json').unlink()
    result = _recover(root)
    assert result.cache.ready_shas() == [] and not entry.entry_dir.exists()
    assert list((root / 'trash').iterdir()) == []


def test_unindexed_entry_failing_the_rehash_is_trashed(root, tmp_path):
    pin, entry = _populated(tmp_path, root)
    (root / 'index.json').unlink()
    target = entry.tree / 'Demo' / 'Content' / 'Maps' / 'Main.umap'
    st = target.stat()
    target.write_bytes(b'Z' * st.st_size)
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))  # passes a stat walk, not a re-hash
    result = _recover(root)
    assert result.cache.ready_shas() == [] and not entry.entry_dir.exists()


def test_forged_self_consistent_entry_owned_by_another_sid_is_not_adopted(root, tmp_path, monkeypatch):
    pin, entry = _populated(tmp_path, root)
    (root / 'index.json').unlink()
    target = entry.tree / 'Demo' / 'Content' / 'Maps' / 'Main.umap'
    target.write_bytes(b'forged')
    record_file = entry.entry_dir / '.complete.json'
    data = json.loads(record_file.read_text(encoding='utf-8'))
    for row in data['files']:
        if row[0] == 'Demo/Content/Maps/Main.umap':
            st = fs_safe.lstat(target)
            row[1], row[2], row[3] = 6, st.st_mtime_ns, h.sha256_file(target)
    data['total_bytes'] = sum(row[1] for row in data['files'])
    record_file.write_text(json.dumps(data), encoding='utf-8')
    real = owner_only_acl.is_owned_by_current_user
    monkeypatch.setattr(owner_only_acl, 'is_owned_by_current_user',
                        lambda path: False if os.fspath(path) == os.fspath(entry.entry_dir) else real(path))
    result = _recover(root)
    assert result.cache.ready_shas() == [] and not entry.entry_dir.exists()
