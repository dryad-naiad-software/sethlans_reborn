# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Pin and publish (Unreal foundation FR-PA-5, FR-PA-8, FR-T-6 "Pin and
publish", AC-23, AC-27).
"""
import hashlib
import json
import os
import shutil
import sys

import pytest

from sethlans_worker_agent.project_archive import cache as cache_module
from sethlans_worker_agent.project_archive import entry_record, fs_safe, integrity, layout, populate, publish
from sethlans_worker_agent.project_archive import safe_extract
from sethlans_worker_agent.project_archive.entry_record import record_digest
from sethlans_worker_agent.project_archive.abort import Abort
from sethlans_worker_agent.project_archive.errors import ArchivePinMismatchError, CacheKeyCollisionError

from . import _helpers as h

SHARED_KEY = 'deadbeefdeadbeef'


@pytest.fixture()
def colliding_keys(monkeypatch):
    """Every SHA-256 maps to the same ``key16`` (a forced 64-bit prefix collision)."""
    for module in (layout, cache_module, publish, integrity):
        monkeypatch.setattr(module, 'key16', lambda sha: SHARED_KEY)


def _obtain(cache, archive, pin, counter=None):
    return populate.obtain_entry(cache, pin, h.local_fetch(cache, archive, pin, counter=counter), Abort(),
                                 10 * h.GIB)


def _two_archives(tmp_path):
    first, second = tmp_path / 'one.zip', tmp_path / 'two.zip'
    pin1 = h.make_zip(first)
    files = dict(h.PROJECT_FILES)
    files['Demo/Content/Extra.uasset'] = b'E' * 100
    pin2 = h.make_zip(second, files=files)
    return (first, pin1), (second, pin2)


def test_pin_mismatch_extracts_nothing(tmp_path):
    archive = tmp_path / 'a.zip'
    h.make_zip(archive)
    cache = h.make_cache(tmp_path)
    wrong = 'f' * 64
    with pytest.raises(ArchivePinMismatchError):
        _obtain(cache, archive, wrong)
    assert list(cache.layout.p.iterdir()) == [] and list(cache.layout.downloads.iterdir()) == []


def test_same_prefix_different_sha_is_never_reused(tmp_path, colliding_keys):
    (first, pin1), (second, pin2) = _two_archives(tmp_path)
    cache = h.make_cache(tmp_path)
    _obtain(cache, first, pin1)
    cache.release(pin1)
    copies = []
    entry = _obtain(cache, second, pin2, counter=copies)
    assert copies == [second] and entry.hit is False
    data = json.loads((cache.layout.p / SHARED_KEY / '.complete.json').read_text(encoding='utf-8'))
    assert data['sha256'] == pin2
    assert cache.state(pin1) is None and cache.state(pin2) == 'ready'
    assert (entry.tree / 'Demo' / 'Content' / 'Extra.uasset').exists()
    assert list(cache.layout.trash.iterdir()) == []
    cache.release(pin2)


def test_collision_with_entry_in_use_fails(tmp_path, colliding_keys):
    (first, pin1), (second, pin2) = _two_archives(tmp_path)
    cache = h.make_cache(tmp_path)
    _obtain(cache, first, pin1)  # still held
    with pytest.raises(CacheKeyCollisionError, match=f'^Unreal cache key collision on {SHARED_KEY}; retry later.$'):
        _obtain(cache, second, pin2)
    data = json.loads((cache.layout.p / SHARED_KEY / '.complete.json').read_text(encoding='utf-8'))
    assert data['sha256'] == pin1 and cache.state(pin1) == 'ready'
    assert [p.name for p in cache.layout.p.iterdir()] == [SHARED_KEY]
    cache.release(pin1)


def test_unindexed_existing_entry_is_always_replaced(tmp_path):
    archive = tmp_path / 'a.zip'
    pin = h.make_zip(archive)
    cache = h.make_cache(tmp_path)
    first = _obtain(cache, archive, pin)
    cache.release(pin)
    marker = first.tree / 'Demo' / 'old-generation'
    cache.load([])  # a crash between publish and the index write: the directory is unindexed
    marker.write_bytes(b'')  # anything in the old directory must not survive
    copies = []
    again = _obtain(cache, archive, pin, counter=copies)
    assert copies == [archive] and again.hit is False and again.entry_dir == first.entry_dir
    assert not marker.exists()
    assert [p.name for p in cache.layout.p.iterdir()] == [pin[:16]]
    assert list(cache.layout.trash.iterdir()) == []
    assert cache.refcount(pin) == 1 and cache.record_digest(pin) == record_digest(again.record)
    cache.release(pin)


def _forge_first_file(entry, payload=b'EVIL'):
    """'Project code': rewrite one tree file and update the record to match (the review probe)."""
    record_path = entry.entry_dir / '.complete.json'
    data = json.loads(record_path.read_text(encoding='utf-8'))
    rel, size = data['files'][0][0], data['files'][0][1]
    target = entry.tree.joinpath(*rel.split('/'))
    evil = (payload * size)[:size]
    target.write_bytes(evil)
    data['files'][0][2] = os.lstat(target).st_mtime_ns
    data['files'][0][3] = hashlib.sha256(evil).hexdigest()
    os.unlink(record_path)
    record_path.write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')
    return target, evil


def test_forged_unindexed_entry_is_replaced_and_rehash_anchors_on_fresh_record(tmp_path):
    """Sec S1 probe: publish never reuses a forged directory; the job's anchor is the fresh record."""
    archive = tmp_path / 'a.zip'
    pin = h.make_zip(archive)
    cache = h.make_cache(tmp_path)
    entry = _obtain(cache, archive, pin)
    cache.release(pin)
    cache.load([])
    target, evil = _forge_first_file(entry)
    again = _obtain(cache, archive, pin)
    assert target.read_bytes() != evil
    assert integrity.check_after_job(cache, pin, again.record).own_ok is True
    _forge_first_file(again)  # forged during the job: the fresh in-memory record catches it
    assert integrity.check_after_job(cache, pin, again.record).own_ok is False
    cache.release(pin)


def test_forged_record_of_an_indexed_entry_is_not_a_hit(tmp_path):
    """Sec S1b: the on-disk record must match the in-memory digest."""
    archive = tmp_path / 'a.zip'
    pin = h.make_zip(archive)
    cache = h.make_cache(tmp_path)
    entry = _obtain(cache, archive, pin)
    cache.release(pin)
    target, evil = _forge_first_file(entry)
    copies = []
    again = _obtain(cache, archive, pin, counter=copies)
    assert copies == [archive] and again.hit is False and target.read_bytes() != evil
    cache.release(pin)


def test_forged_record_of_another_entry_fails_its_stat_walk(tmp_path):
    (first, pin1), (second, pin2) = _two_archives(tmp_path)
    cache = h.make_cache(tmp_path)
    own = _obtain(cache, first, pin1)
    other = _obtain(cache, second, pin2)
    cache.release(pin2)
    _forge_first_file(other)
    report = integrity.check_after_job(cache, pin1, own.record)
    assert report.own_ok is True and report.failures == {pin2[:16]: 1}
    assert cache.state(pin2) is None
    cache.release(pin1)


def test_identical_entry_in_use_is_shared_with_the_fresh_record(tmp_path):
    archive = tmp_path / 'a.zip'
    pin = h.make_zip(archive)
    cache = h.make_cache(tmp_path)
    held = _obtain(cache, archive, pin)
    tmp = cache.layout.new_tmp_entry_dir(pin)
    fs_safe.ensure_owned_dir(tmp)
    (tmp / 'tree').mkdir()
    with safe_extract.OpenedArchive(archive, 10 * h.GIB) as opened:
        result = opened.extract(tmp / 'tree')
    fresh = entry_record.CompleteRecord(pin, held.record.source, result.files, result.dirs, result.total_bytes)
    entry_record.write_record(tmp, fresh)
    assert publish.publish(cache, tmp, fresh) is False
    assert cache.refcount(pin) == 2 and not tmp.exists()
    cache.release(pin)
    cache.release(pin)


def test_publish_refuses_a_key_being_removed(tmp_path):
    archive = tmp_path / 'a.zip'
    pin = h.make_zip(archive)
    cache = h.make_cache(tmp_path)
    with cache.lock:
        cache.removing.add(pin[:16])
    with pytest.raises(CacheKeyCollisionError):
        _obtain(cache, archive, pin)
    assert list(cache.layout.p.iterdir()) == []


def test_invalid_existing_entry_is_replaced(tmp_path):
    archive = tmp_path / 'a.zip'
    pin = h.make_zip(archive)
    cache = h.make_cache(tmp_path)
    first = _obtain(cache, archive, pin)
    cache.release(pin)
    cache.load([])
    (first.tree / 'Demo' / 'Content' / 'Maps' / 'Main.umap').write_bytes(b'tampered')
    entry = _obtain(cache, archive, pin)
    assert (entry.tree / 'Demo' / 'Content' / 'Maps' / 'Main.umap').read_bytes() == h.PROJECT_FILES[
        'Demo/Content/Maps/Main.umap']
    cache.release(pin)


def test_entry_directory_swapped_for_a_link_is_not_a_hit(tmp_path):
    archive = tmp_path / 'a.zip'
    pin = h.make_zip(archive)
    cache = h.make_cache(tmp_path)
    first = _obtain(cache, archive, pin)
    cache.release(pin)
    decoy = tmp_path / 'decoy'
    shutil.copytree(first.entry_dir, decoy)
    shutil.rmtree(first.entry_dir)
    if sys.platform == 'win32':
        import _winapi
        _winapi.CreateJunction(str(decoy), str(first.entry_dir))
    else:
        os.symlink(decoy, first.entry_dir, target_is_directory=True)
    copies = []
    entry = _obtain(cache, archive, pin, counter=copies)
    assert copies == [archive] and entry.hit is False
    assert not entry.entry_dir.is_symlink() and not os.path.isjunction(entry.entry_dir)
    assert (decoy / 'tree').is_dir()  # the link target was not deleted
    cache.release(pin)


def test_hit_verification_failure_quarantines_and_repopulates(tmp_path):
    archive = tmp_path / 'a.zip'
    pin = h.make_zip(archive)
    cache = h.make_cache(tmp_path)
    first = _obtain(cache, archive, pin)
    cache.release(pin)
    (first.tree / 'Demo' / 'Demo.uproject').write_bytes(b'{"changed": true}')
    copies = []
    entry = _obtain(cache, archive, pin, counter=copies)
    assert copies == [archive] and entry.hit is False
    assert (entry.tree / 'Demo' / 'Demo.uproject').read_bytes() == h.PROJECT_FILES['Demo/Demo.uproject']
    cache.release(pin)
