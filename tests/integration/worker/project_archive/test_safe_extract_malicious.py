# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Malicious archives (Unreal foundation FR-PA-6/7, FR-T-6, AC-10): every
case raises ``UnsafeArchiveError`` through the real populate path and
leaves no ``p/<key>`` directory, no download and no reservation behind.
"""
import stat
import struct
import zipfile

import pytest

from sethlans_worker_agent.project_archive import populate
from sethlans_worker_agent.project_archive.abort import Abort
from sethlans_worker_agent.project_archive.errors import UnsafeArchiveError

from . import _helpers as h

GOOD = {'Demo/Demo.uproject': b'{}'}


def _files(extra):
    files = dict(GOOD)
    files.update(extra)
    return lambda path: h.make_zip(path, files=files)


def _infos(*pairs):
    return lambda path: h.make_zip_infos(path, [(h.info('Demo/Demo.uproject'), b'{}')] + list(pairs))


def _encrypted(path):
    h.make_zip(path, files={'Demo/Demo.uproject': b'{}', 'Demo/secret.bin': b'x' * 10})
    h.set_flag_bits(path, 'Demo/secret.bin', 0x801)


def _nul_name(path):
    h.make_zip(path, files={'Demo/Demo.uproject': b'{}', 'Demo/aQb.txt': b'x'})
    h.replace_name_bytes(path, b'Demo/aQb.txt', b'Demo/a\x00b.txt')


def _lying_local_size(path):
    h.make_zip(path, files={'Demo/Demo.uproject': b'{}', 'Demo/data.bin': b'y' * 1000})
    offset = h.local_header_offset(path, 'Demo/data.bin')
    h.patch_bytes(path, offset + 22, struct.pack('<L', 10))


def _raw_name(written, raw):
    """A raw ``orig_filename`` (backslashes) that zipfile itself would never write."""
    def build(path):
        h.make_zip(path, files={'Demo/Demo.uproject': b'{}', written: b'x'})
        h.replace_name_bytes(path, written.encode(), raw)
    return build


def _ratio_bomb(path):
    h.make_zip(path, files={'Demo/Demo.uproject': b'{}', 'Demo/zeros.bin': b'\x00' * (64 * 1024 * 1024)})


CASES = {
    'traversal': _files({'../evil.txt': b'x'}),
    'nested-traversal': _files({'Demo/../../evil.txt': b'x'}),
    'dot-component': _files({'Demo/./x.txt': b'x'}),
    'absolute': _files({'/abs.txt': b'x'}),
    'drive': _files({'C:/x.txt': b'x'}),
    'drive-relative': _files({'C:x.txt': b'x'}),
    'unc-like': _files({'//server/share/x.txt': b'x'}),
    'empty-component': _files({'Demo//x.txt': b'x'}),
    'symlink-unix': _infos((h.symlink_info(3), b'/etc/passwd')),
    'symlink-any-system': _infos((h.symlink_info(0), b'C:/Windows')),
    'fifo-unix': _infos((h.info('Demo/fifo', create_system=3, mode=stat.S_IFIFO | 0o644), b'')),
    'case-duplicate': _files({'Demo/a.txt': b'1', 'Demo/A.TXT': b'2'}),
    'file-dir-conflict': _files({'Demo/x': b'1', 'Demo/x/y.txt': b'2'}),
    'encrypted': _encrypted,
    'bzip2': _infos((h.info('Demo/b.bin', method=zipfile.ZIP_BZIP2), b'b' * 100)),
    'lzma': _infos((h.info('Demo/l.bin', method=zipfile.ZIP_LZMA), b'l' * 100)),
    'ratio-bomb': _ratio_bomb,
    'lying-local-header': _lying_local_size,
    'reserved-con': _files({'Demo/CON.txt': b'x'}),
    'reserved-conin': _files({'Demo/CONIN$': b'x'}),
    'reserved-com-superscript': _files({'Demo/COM\u00b9.log': b'x'}),
    'reserved-aux-dir': _files({'Demo/aux/file.txt': b'x'}),
    'trailing-dot': _files({'Demo/x.': b'x'}),
    'trailing-space': _files({'Demo/x ': b'x'}),
    'stream-colon': _files({'Demo/a.txt:stream': b'x'}),
    'nul': _nul_name,
    'short-name': _files({'Demo/PROGRA~1/x.txt': b'x'}),
    'control-char': _files({'Demo/a\x07.txt': b'x'}),
    'long-component': _files({'Demo/' + 'n' * 256: b'x'}),
    'backslash-traversal': _raw_name('Demo/../../evil.txt', rb'Demo\..\..\evil.txt'),
    'backslash-unc': _raw_name('//srv/s/x', rb'\\srv\s\x'),
}


REASONS = {
    'traversal': "'..'", 'nested-traversal': "'..'", 'dot-component': "'.'", 'absolute': 'absolute',
    'drive': 'drive', 'drive-relative': 'drive', 'unc-like': 'UNC-like', 'empty-component': "''",
    'symlink-unix': 'symlink', 'symlink-any-system': 'symlink', 'fifo-unix': 'special file',
    'case-duplicate': 'duplicate', 'file-dir-conflict': 'both a file and a directory',
    'encrypted': 'encrypted', 'bzip2': 'compression method 12', 'lzma': 'compression method 14',
    'ratio-bomb': 'ratio above 200', 'lying-local-header': 'local header disagrees',
    'reserved-con': 'reserved', 'reserved-conin': 'reserved', 'reserved-com-superscript': 'reserved',
    'reserved-aux-dir': 'reserved', 'trailing-dot': 'ends with', 'trailing-space': 'ends with',
    'stream-colon': '<>:', 'nul': 'NUL', 'short-name': '8.3', 'control-char': 'control',
    'long-component': 'longer than 255', 'backslash-traversal': "'..'", 'backslash-unc': 'UNC-like',
}


def _assert_clean(cache, pin):
    assert not cache.entry_dir(pin).exists()
    assert list(cache.layout.p.iterdir()) == []
    assert list(cache.layout.downloads.iterdir()) == []
    assert cache.ledger.in_flight_reserved == 0
    assert cache.refcount(pin) == 0


@pytest.mark.parametrize('case', sorted(CASES))
def test_malicious_archive_rejected(case, tmp_path):
    share = tmp_path / 'share'
    share.mkdir()
    archive = share / 'bad.zip'
    CASES[case](archive)
    pin = h.sha256_file(archive)
    cache = h.make_cache(tmp_path)
    with pytest.raises(UnsafeArchiveError) as caught:
        populate.obtain_entry(cache, pin, h.local_fetch(cache, archive, pin), Abort(), 10 * h.GIB)
    assert REASONS[case] in caught.value.reason
    _assert_clean(cache, pin)


def test_total_uncompressed_cap(tmp_path):
    archive = tmp_path / 'big.zip'
    pin = h.make_zip(archive)
    cache = h.make_cache(tmp_path)
    with pytest.raises(UnsafeArchiveError, match='exceeds the limit'):
        populate.obtain_entry(cache, pin, h.local_fetch(cache, archive, pin), Abort(), 100)
    _assert_clean(cache, pin)


def test_directory_entries_with_trailing_slash_pass(tmp_path):
    """The PoC shape: 284 directory entries with a trailing '/' (FR-PA-6)."""
    archive = tmp_path / 'dirs.zip'
    pin = h.make_zip(archive, dirs=('Demo/', 'Demo/Config/', 'Demo/Content/', 'Demo/Content/Empty/'))
    cache = h.make_cache(tmp_path)
    entry = populate.obtain_entry(cache, pin, h.local_fetch(cache, archive, pin), Abort(), 10 * h.GIB)
    assert (entry.tree / 'Demo' / 'Content' / 'Empty').is_dir()
    assert entry.record.dirs == ('Demo', 'Demo/Config', 'Demo/Content', 'Demo/Content/Empty')
    cache.release(pin)
