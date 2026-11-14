# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Working-copy materialization (Unreal foundation FR-PA-13/14/15/20,
FR-T-6 "Working copy", AC-11).
"""
import hashlib
import json
import os

import pytest

from sethlans_worker_agent.project_archive import eviction, fs_safe, overlay, populate, working_copy
from sethlans_worker_agent.project_archive.abort import Abort
from sethlans_worker_agent.project_archive.errors import UprojectNotFoundError, WorkingCopyError

from . import _helpers as h

UPROJECT = 'Demo/Demo.uproject'
MAP = ('Content', 'Maps', 'Main.umap')


@pytest.fixture()
def entry(tmp_path):
    archive = tmp_path / 'demo.zip'
    pin = h.make_zip(archive)
    cache = h.make_cache(tmp_path)
    acquired = populate.obtain_entry(cache, pin, h.local_fetch(cache, archive, pin), Abort(), 10 * h.GIB)
    yield cache, acquired
    cache.release(pin)


def _staged_overlay(tmp_path, cache, files):
    src = tmp_path / 'share' / 'fixes'
    for rel, data in files.items():
        path = src.joinpath(*rel.split('/'))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return overlay.stage_overlay(str(src), cache.layout.overlay_dir(1, 'abcdef01'), h.spec_digest(src), Abort(),
                                 reserve=lambda n: eviction.reserve(cache, n), share_validator=lambda p: str(p),
                                 digest_fn=h.spec_digest)


def _build(cache, acquired, staged=None, **kwargs):
    return working_copy.materialize(cache.layout, acquired.tree, UPROJECT, staged, sha256=acquired.sha256,
                                    overlay_sha256=staged.digest if staged else '',
                                    reserve=lambda n: eviction.reserve(cache, n), **kwargs)


def test_hardlinks_share_the_inode_and_globs_are_copies(entry):
    cache, acquired = entry
    copy = _build(cache, acquired)
    assert copy.mode == 'hardlink' and copy.files == 4
    assert copy.root == cache.layout.w / f"{acquired.sha256[:16]}00000000-s0"
    assert copy.project_dir == copy.root / 'Demo' and copy.uproject.name == 'Demo.uproject'
    linked, pristine = copy.project_dir.joinpath(*MAP), acquired.tree.joinpath('Demo', *MAP)
    assert os.stat(linked).st_ino == os.stat(pristine).st_ino and os.stat(pristine).st_nlink == 2
    for rel in ('Demo.uproject', 'Config/DefaultEngine.ini'):
        assert os.stat(copy.project_dir / rel).st_nlink == 1
    assert cache.ledger.in_flight_reserved == 0
    assert working_copy.remove(copy, cache.layout)
    assert os.stat(pristine).st_nlink == 1 and not copy.root.exists()


def test_overlay_replacement_leaves_pristine_bytes_unchanged(tmp_path, entry):
    cache, acquired = entry
    pristine = acquired.tree.joinpath('Demo', *MAP)
    before = hashlib.sha256(pristine.read_bytes()).hexdigest()
    staged = _staged_overlay(tmp_path, cache, {'Content/Maps/main.UMAP': b'overlay map',
                                               'Content/Python/fix.py': b'print(1)'})
    copy = _build(cache, acquired, staged)
    target = copy.project_dir.joinpath(*MAP)
    assert target.read_bytes() == b'overlay map' and os.stat(target).st_nlink == 1
    assert (copy.project_dir / 'Content' / 'Python' / 'fix.py').read_bytes() == b'print(1)'
    assert hashlib.sha256(pristine.read_bytes()).hexdigest() == before
    assert copy.root.name == f"{acquired.sha256[:16]}{staged.digest[:8]}-s0"


def test_copy_mode_copies_everything(entry):
    cache, acquired = entry
    copy = _build(cache, acquired, mode='copy')
    assert copy.mode == 'copy'
    assert os.stat(copy.project_dir.joinpath(*MAP)).st_nlink == 1


def test_link_failure_falls_back_to_copy(entry):
    cache, acquired = entry

    def no_links(src, dst):
        raise OSError(18, 'cross-device link')

    copy = _build(cache, acquired, link=no_links)
    assert copy.mode == 'copy(fallback)'
    assert copy.project_dir.joinpath(*MAP).read_bytes() == h.PROJECT_FILES['Demo/' + '/'.join(MAP)]
    assert cache.ledger.in_flight_reserved == 0


def test_relpaths_come_from_the_tree_walk(entry):
    cache, acquired = entry
    record_path = acquired.entry_dir / '.complete.json'
    data = json.loads(record_path.read_text(encoding='utf-8'))
    data['files'].append(['Demo/Content/Injected.uasset', 1, 1, '0' * 64, 0])
    record_path.write_text(json.dumps(data), encoding='utf-8')
    copy = _build(cache, acquired)
    assert not (copy.project_dir / 'Content' / 'Injected.uasset').exists()


def test_unclearable_stable_path_falls_back_to_suffix(entry, monkeypatch):
    cache, acquired = entry
    first = _build(cache, acquired)
    real_move = fs_safe.move_aside
    monkeypatch.setattr(working_copy.fs_safe, 'move_aside',
                        lambda path, trash: None if path == first.root else real_move(path, trash))
    second = _build(cache, acquired)
    assert second.root == first.root.with_name(first.root.name + '-1')
    assert first.root.exists()


def test_all_suffixes_taken_fails(entry, monkeypatch):
    cache, acquired = entry
    name = cache.layout.work_base_name(acquired.sha256, '', 0)
    for suffix in [''] + [f'-{n}' for n in range(1, 10)]:
        (cache.layout.w / f'{name}{suffix}').mkdir()
    monkeypatch.setattr(working_copy.fs_safe, 'move_aside', lambda path, trash: None)
    with pytest.raises(WorkingCopyError, match='^Cannot clear Unreal working copy; orphaned process may hold files.$'):
        _build(cache, acquired)


def test_path_length_guard(entry):
    cache, acquired = entry
    with pytest.raises(WorkingCopyError, match='would exceed MAX_PATH'):
        _build(cache, acquired, slot=10 ** 240, long_paths=lambda: False)
    copy = _build(cache, acquired, long_paths=lambda: False)
    assert copy.mode == 'hardlink'


def test_uproject_lookup_is_case_insensitive_second(tmp_path, entry):
    cache, acquired = entry
    copy = working_copy.materialize(cache.layout, acquired.tree, 'demo/DEMO.uproject', None, sha256=acquired.sha256,
                                    reserve=lambda n: eviction.reserve(cache, n))
    assert copy.uproject.name == 'Demo.uproject'
    with pytest.raises(UprojectNotFoundError, match="^uproject 'Demo/Missing.uproject' not found in archive$"):
        working_copy.materialize(cache.layout, acquired.tree, 'Demo/Missing.uproject', None,
                                 sha256=acquired.sha256, reserve=lambda n: eviction.reserve(cache, n))


def test_overlay_conflicting_with_a_directory_fails(tmp_path, entry):
    cache, acquired = entry
    staged = _staged_overlay(tmp_path, cache, {'Content/Maps/Main.umap/x.txt': b'x'})
    with pytest.raises(WorkingCopyError, match='conflicts'):
        _build(cache, acquired, staged)


def test_dirs_ue_rewrites_are_copied_not_linked(tmp_path):
    """Sec S7: Saved/, Intermediate/, DerivedDataCache/ and Plugins/*/Config are copies."""
    archive = tmp_path / 'demo.zip'
    files = dict(h.PROJECT_FILES)
    files.update({'Demo/Saved/Config/WindowsEditor/Editor.ini': b'[x]', 'Demo/Intermediate/a.bin': b'i',
                  'Demo/DerivedDataCache/d.ddp': b'd', 'Demo/Plugins/P/Config/DefaultP.ini': b'[p]',
                  'Demo/Plugins/P/Content/p.uasset': b'pc'})
    pin = h.make_zip(archive, files=files)
    cache = h.make_cache(tmp_path)
    acquired = populate.obtain_entry(cache, pin, h.local_fetch(cache, archive, pin), Abort(), 10 * h.GIB)
    copy = _build(cache, acquired)
    for rel in ('Saved/Config/WindowsEditor/Editor.ini', 'Intermediate/a.bin', 'DerivedDataCache/d.ddp',
                'Plugins/P/Config/DefaultP.ini'):
        assert os.stat(copy.project_dir / rel).st_nlink == 1, rel
    assert os.stat(copy.project_dir / 'Plugins' / 'P' / 'Content' / 'p.uasset').st_nlink == 2
    working_copy.remove(copy, cache.layout)
    cache.release(pin)


@pytest.mark.parametrize('rel,expected', [
    ('Demo.uproject', True), ('Sub/Demo.uproject', False), ('config/x.ini', True), ('Saved/a', True),
    ('Plugins/A/Config/x.ini', True), ('Plugins/A/Content/x.uasset', False), ('Content/Config', False),
    ('Plugins/Config', False),
])
def test_copy_glob_table(rel, expected):
    assert working_copy.is_copy_glob(rel) is expected


def test_uproject_path_must_have_the_suffix(entry):
    cache, acquired = entry
    with pytest.raises(UprojectNotFoundError):
        working_copy.materialize(cache.layout, acquired.tree, 'Demo/Content/Maps/Main.umap', None,
                                 sha256=acquired.sha256, reserve=lambda n: eviction.reserve(cache, n))


def test_reused_stable_path_is_no_longer_queued(entry, monkeypatch):
    """Conc C4: a path queued as stale is dropped from the queue once it is chosen again."""
    cache, acquired = entry
    first = _build(cache, acquired)
    with working_copy._stale_lock:
        working_copy._stale.add(first.root)
    monkeypatch.setattr(working_copy, '_retry_stale', lambda: None)
    second = _build(cache, acquired)
    assert second.root == first.root
    with working_copy._stale_lock:
        assert first.root not in working_copy._stale
