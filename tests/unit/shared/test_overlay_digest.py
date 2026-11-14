# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``shared.unreal.overlay_digest`` (Unreal foundation FR-MU-5, D3, FR-T-4).
"""
import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from shared.unreal import overlay_digest as od


def _tree(root):
    (root / 'Config').mkdir(parents=True)
    (root / 'Config' / 'DefaultEngine.ini').write_bytes(b'[x]\r\n')
    (root / 'Content' / 'Python').mkdir(parents=True)
    (root / 'Content' / 'Python' / 'a.py').write_bytes(b'print(1)\n')
    (root / 'Content' / 'Python' / 'B.py').write_bytes(b'')
    (root / 'Empty').mkdir()
    return root


def _expected(entries):
    payload = json.dumps(entries, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def test_digest_matches_the_documented_formula(tmp_path):
    root = _tree(tmp_path / 'overlay')
    result = od.compute_overlay_digest(root)
    entries = [
        ['Config/DefaultEngine.ini', 5, hashlib.sha256(b'[x]\r\n').hexdigest()],
        ['Content/Python/B.py', 0, hashlib.sha256(b'').hexdigest()],
        ['Content/Python/a.py', 9, hashlib.sha256(b'print(1)\n').hexdigest()],
    ]
    assert [list(f) for f in result.files] == entries
    assert result.digest == _expected(entries)


def test_digest_is_stable_and_content_sensitive(tmp_path):
    root = _tree(tmp_path / 'overlay')
    first = od.compute_overlay_digest(root).digest
    assert od.compute_overlay_digest(str(root)).digest == first
    (root / 'Content' / 'Python' / 'a.py').write_bytes(b'print(2)\n')
    assert od.compute_overlay_digest(root).digest != first


def test_empty_directory(tmp_path):
    assert od.compute_overlay_digest(tmp_path).digest == _expected([])


def test_root_must_be_a_directory(tmp_path):
    target = tmp_path / 'f.txt'
    target.write_bytes(b'x')
    with pytest.raises(od.OverlayDigestError):
        od.compute_overlay_digest(target)


def test_symlinks_are_refused(tmp_path):
    root = _tree(tmp_path / 'overlay')
    try:
        os.symlink(root / 'Config' / 'DefaultEngine.ini', root / 'link.ini')
    except (OSError, NotImplementedError):
        pytest.skip('symlinks unavailable')
    with pytest.raises(od.OverlayDigestError, match='link'):
        od.compute_overlay_digest(root)


def test_cli_prints_the_digest(tmp_path, capsys):
    root = _tree(tmp_path / 'overlay')
    assert od.main([str(root)]) == 0
    assert capsys.readouterr().out.strip() == od.compute_overlay_digest(root).digest


def test_cli_errors(tmp_path, capsys):
    assert od.main([]) == 2
    assert od.main([str(tmp_path / 'missing')]) == 1
    assert 'error' in capsys.readouterr().err


def test_a_file_swapped_after_listing_is_refused(tmp_path, monkeypatch):
    root = _tree(tmp_path / 'overlay')
    real_fstat = os.fstat

    def other_file(fd):
        st = real_fstat(fd)
        return SimpleNamespace(st_mode=st.st_mode, st_ino=st.st_ino + 1, st_dev=st.st_dev,
                               st_size=st.st_size, st_file_attributes=0)

    monkeypatch.setattr(od.os, 'fstat', other_file)
    with pytest.raises(od.OverlayDigestError, match='between listing and opening'):
        od.compute_overlay_digest(root)


def test_nesting_is_capped(tmp_path):
    root = tmp_path / 'overlay'
    deep = root.joinpath(*(['d'] * (od.MAX_DEPTH + 1)))
    deep.mkdir(parents=True)
    with pytest.raises(od.OverlayDigestError, match='nested deeper'):
        od.compute_overlay_digest(root)
    ok_root = tmp_path / 'ok'
    ok_root.joinpath(*(['d'] * od.MAX_DEPTH)).mkdir(parents=True)
    assert od.compute_overlay_digest(ok_root).files == ()


def test_cli_reports_digest_errors_without_a_traceback(tmp_path, capsys):
    root = tmp_path / 'overlay'
    root.joinpath(*(['d'] * (od.MAX_DEPTH + 1))).mkdir(parents=True)
    assert od.main([str(root)]) == 1
    err = capsys.readouterr().err
    assert err.startswith('error: ') and 'Traceback' not in err
