# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Extraction outcomes and atomic publish (Unreal foundation FR-PA-7,
FR-PA-8, FR-T-6 "Extraction outcomes").
"""
import hashlib
import json
import threading

import pytest

from sethlans_worker_agent.project_archive import populate, safe_extract
from sethlans_worker_agent.project_archive.abort import Abort
from sethlans_worker_agent.project_archive.errors import AbortedError

from . import _helpers as h


def _obtain(cache, archive, pin):
    return populate.obtain_entry(cache, pin, h.local_fetch(cache, archive, pin), Abort(), 10 * h.GIB)


def test_valid_archive_publishes_with_record(tmp_path):
    archive = tmp_path / 'demo.zip'
    pin = h.make_zip(archive, dirs=('Demo/',))
    cache = h.make_cache(tmp_path)
    entry = _obtain(cache, archive, pin)
    assert entry.hit is False and entry.entry_dir == cache.layout.p / pin[:16]
    data = json.loads((entry.entry_dir / '.complete.json').read_text(encoding='utf-8'))
    assert data['sha256'] == pin and data['source']['size'] == archive.stat().st_size
    for relpath, size, _mtime, sha, _attrs in data['files']:
        content = h.PROJECT_FILES[relpath]
        assert size == len(content) and sha == hashlib.sha256(content).hexdigest()
        assert (entry.tree / relpath).read_bytes() == content
    assert data['total_bytes'] == sum(len(v) for v in h.PROJECT_FILES.values())
    assert list(cache.layout.downloads.iterdir()) == []  # the local zip is deleted after publish
    assert [p.name for p in cache.layout.p.iterdir()] == [pin[:16]]
    assert cache.refcount(pin) == 1 and cache.ledger.pristine_total == data['total_bytes']
    assert cache.ledger.in_flight_reserved == 0
    cache.release(pin)
    assert cache.refcount(pin) == 0


def test_second_job_is_a_hit_without_share_access(tmp_path):
    archive = tmp_path / 'demo.zip'
    pin = h.make_zip(archive)
    cache = h.make_cache(tmp_path)
    copies = []
    _obtain(cache, archive, pin)
    cache.release(pin)
    entry = populate.obtain_entry(cache, pin, h.local_fetch(cache, archive, pin, counter=copies), Abort(),
                                  10 * h.GIB)
    assert entry.hit is True and copies == []
    cache.release(pin)


def test_mid_extract_exception_leaves_no_entry(tmp_path, monkeypatch):
    archive = tmp_path / 'demo.zip'
    pin = h.make_zip(archive)
    cache = h.make_cache(tmp_path)
    original = safe_extract.OpenedArchive._extract_file
    calls = []

    def failing(self, *args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise OSError("disk exploded")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(safe_extract.OpenedArchive, '_extract_file', failing)
    with pytest.raises(OSError, match='disk exploded'):
        _obtain(cache, archive, pin)
    assert list(cache.layout.p.iterdir()) == []
    assert list(cache.layout.downloads.iterdir()) == []
    assert cache.ledger.in_flight_reserved == 0 and cache.state(pin) is None


def test_cancel_during_extract_leaves_no_entry(tmp_path, monkeypatch):
    archive = tmp_path / 'demo.zip'
    pin = h.make_zip(archive)
    cache = h.make_cache(tmp_path)

    cancel = threading.Event()
    original = safe_extract.OpenedArchive._extract_file

    def cancel_on_second_file(self, entry, *args, **kwargs):
        if entry.relpath.endswith('.ini'):
            cancel.set()
        return original(self, entry, *args, **kwargs)

    monkeypatch.setattr(safe_extract.OpenedArchive, '_extract_file', cancel_on_second_file)
    with pytest.raises(AbortedError) as caught:
        populate.obtain_entry(cache, pin, h.local_fetch(cache, archive, pin), Abort(cancel=cancel), 10 * h.GIB)
    assert caught.value.cause == 'canceled'
    assert list(cache.layout.p.iterdir()) == []
    assert list(cache.layout.downloads.iterdir()) == []


def test_real_part_entry_next_to_its_namesake(tmp_path):
    archive = tmp_path / 'part.zip'
    files = {'Demo/Demo.uproject': b'{}', 'Demo/x': b'real x', 'Demo/x.part': b'real x.part'}
    pin = h.make_zip(archive, files=files)
    cache = h.make_cache(tmp_path)
    entry = _obtain(cache, archive, pin)
    assert (entry.tree / 'Demo' / 'x').read_bytes() == b'real x'
    assert (entry.tree / 'Demo' / 'x.part').read_bytes() == b'real x.part'
    assert not any(p.name.startswith('.sx-') for p in (entry.tree / 'Demo').iterdir())
    cache.release(pin)


def test_stored_entries_extract(tmp_path):
    archive = tmp_path / 'stored.zip'
    pin = h.make_zip(archive, method=safe_extract.zipfile.ZIP_STORED)
    cache = h.make_cache(tmp_path)
    entry = _obtain(cache, archive, pin)
    assert (entry.tree / 'Demo' / 'Content' / 'Maps' / 'Main.umap').read_bytes() == h.PROJECT_FILES[
        'Demo/Content/Maps/Main.umap']
    cache.release(pin)
