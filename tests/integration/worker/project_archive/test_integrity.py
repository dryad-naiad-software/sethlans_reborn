# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Post-job pristine integrity (Unreal foundation FR-PA-16, Sec N8, FR-T-5
"Integrity", FR-T-6 "Integrity").
"""
import os

from sethlans_worker_agent.project_archive import integrity, populate
from sethlans_worker_agent.project_archive.abort import Abort

from . import _helpers as h

MAP = ('Demo', 'Content', 'Maps', 'Main.umap')


def _entry(tmp_path, cache, name='a', payload=b''):
    archive = tmp_path / f'{name}.zip'
    files = dict(h.PROJECT_FILES)
    files['Demo/Content/Unique.uasset'] = name.encode() + payload
    pin = h.make_zip(archive, files=files)
    return populate.obtain_entry(cache, pin, h.local_fetch(cache, archive, pin), Abort(), 10 * h.GIB)


def _tamper_same_size(path):
    st = os.stat(path)
    data = bytearray(path.read_bytes())
    data[0] ^= 0xFF
    path.write_bytes(bytes(data))
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def test_clean_entries_report_ok(tmp_path):
    cache = h.make_cache(tmp_path)
    own = _entry(tmp_path, cache, 'own')
    other = _entry(tmp_path, cache, 'other')
    cache.release(other.sha256)
    report = integrity.check_after_job(cache, own.sha256, own.record)
    assert report.own_ok and report.summary == 'pristine_integrity=ok'
    cache.release(own.sha256)


def test_same_size_change_with_restored_mtime_is_caught_by_the_rehash(tmp_path):
    cache = h.make_cache(tmp_path)
    own = _entry(tmp_path, cache)
    _tamper_same_size(own.tree.joinpath(*MAP))
    assert integrity.rehash_mismatches(own.entry_dir, own.record) == ['Demo/Content/Maps/Main.umap']
    report = integrity.check_after_job(cache, own.sha256, own.record)
    assert report.own_ok is False
    assert report.summary == f'pristine_integrity=FAILED ({own.sha256[:16]}: 1 files)'
    assert integrity.OWN_FAILURE_MESSAGE == (
        "Pristine cache integrity check failed for this job's project; output discarded.")
    assert cache.state(own.sha256) == 'quarantined' and own.entry_dir.exists()  # deferred: still held
    cache.release(own.sha256)
    assert cache.state(own.sha256) is None and not own.entry_dir.exists()


def test_rehash_uses_the_record_captured_at_acquire(tmp_path):
    """Project code rewriting .complete.json to match its change does not hide it."""
    cache = h.make_cache(tmp_path)
    own = _entry(tmp_path, cache)
    target = own.tree.joinpath(*MAP)
    target.write_bytes(b'rewritten')
    record_file = own.entry_dir / '.complete.json'
    text = record_file.read_text(encoding='utf-8')
    forged = text.replace('"Demo/Content/Maps/Main.umap",4096', '"Demo/Content/Maps/Main.umap",9')
    assert forged != text
    record_file.write_text(forged, encoding='utf-8')
    assert integrity.check_after_job(cache, own.sha256, own.record).own_ok is False
    cache.release(own.sha256)


def test_failure_on_another_entry_is_summary_only(tmp_path):
    cache = h.make_cache(tmp_path)
    own = _entry(tmp_path, cache, 'own')
    other = _entry(tmp_path, cache, 'other')
    cache.release(other.sha256)
    (other.tree / 'Demo' / 'Content' / 'Extra.bin').write_bytes(b'planted')
    report = integrity.check_after_job(cache, own.sha256, own.record)
    assert report.own_ok is True
    assert report.summary == f'pristine_integrity=FAILED ({other.sha256[:16]}: 1 files)'
    assert cache.state(other.sha256) is None and not other.entry_dir.exists()
    cache.release(own.sha256)


def test_walking_other_entries_does_not_refresh_their_lru_time(tmp_path):
    cache = h.make_cache(tmp_path)
    own = _entry(tmp_path, cache, 'own')
    other = _entry(tmp_path, cache, 'other')
    cache.release(other.sha256)
    with cache.lock:
        before = cache.entry_locked(other.sha256).last_used
    cache._clock = lambda: before + 1000.0
    integrity.check_after_job(cache, own.sha256, own.record)
    with cache.lock:
        assert cache.entry_locked(other.sha256).last_used == before
        assert cache.entry_locked(other.sha256).refcount == 0
    cache.release(own.sha256)


def test_skipped_with_survivors():
    assert integrity.skipped('survivors').summary == 'pristine_integrity=skipped(survivors)'
