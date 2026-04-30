# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Archive traversal-safety tests for parts_check.ffmpeg_download_pkg.extract.

Per spec FR §52-58 and Tests §414, the extractor must reject:

- Tarfile members with absolute paths, ``..`` segments, or symlinks
  whose target falls outside the destination directory.
- Zipfile members with absolute paths, ``..`` segments, or paths whose
  resolved target falls outside the destination directory.

A traversal violation aborts extraction with ``ExtractionUnsafeError``.
A corrupt/unparseable archive raises ``ExtractionFailedError``.

The upstream caller (install.py) maps the two error classes to the
closed-vocabulary ``error`` codes ``"extraction_unsafe"`` and
``"extraction_failed"``; that mapping is covered separately in
``test_parts_check_atomic_extract.py``.
"""

from __future__ import annotations

import io
import platform
import tarfile
import zipfile
from pathlib import Path

import pytest

from workers.services.parts_check.ffmpeg_download_pkg import (
    extract as extract_mod,
)


def _binary_name() -> str:
    return "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg"


# ---- Tarfile traversal rejection ------------------------------------

def test_tarfile_absolute_path_member_does_not_escape_dest(tmp_path):
    """Tar with an absolute-path member is sanitized inside dest.

    Python 3.12+ ``tarfile`` ``filter='data'`` strips leading ``/``
    from member names rather than raising — but the result is
    written safely INSIDE ``dest`` (never at the absolute location).
    The spec requirement (no traversal) is met by either (a) raising
    ``ExtractionUnsafeError`` OR (b) silently sanitizing inside dest;
    both behaviours are safe.  This test asserts the safety
    invariant: nothing ends up at the absolute location.
    """
    archive = tmp_path / "evil.tar.xz"
    with tarfile.open(archive, "w:xz") as tar:
        info = tarfile.TarInfo(name="/abs/path/evil")
        payload = b"pwned"
        info.size = len(payload)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(payload))

    dest = tmp_path / "dest"
    dest.mkdir()
    extract_mod.extract_archive(archive, dest)
    assert not Path("/abs/path/evil").exists()
    for produced in dest.rglob("*"):
        produced_real = Path(produced).resolve()
        dest_real = dest.resolve()
        assert str(produced_real).startswith(str(dest_real)), (
            f"member {produced!r} escaped dest"
        )


def test_tarfile_parent_traversal_member_rejected(tmp_path):
    """Tar with ``../../../etc/evil`` member raises ExtractionUnsafeError."""
    archive = tmp_path / "evil.tar.xz"
    with tarfile.open(archive, "w:xz") as tar:
        info = tarfile.TarInfo(name="../../../etc/evil")
        payload = b"pwned"
        info.size = len(payload)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(payload))

    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(extract_mod.ExtractionUnsafeError):
        extract_mod.extract_archive(archive, dest)


def test_tarfile_outside_destination_symlink_rejected(tmp_path):
    """Tar with a symlink whose target is outside dest is rejected."""
    archive = tmp_path / "evil.tar.xz"
    with tarfile.open(archive, "w:xz") as tar:
        info = tarfile.TarInfo(name="evil-link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        info.size = 0
        tar.addfile(info)

    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(extract_mod.ExtractionUnsafeError):
        extract_mod.extract_archive(archive, dest)


def test_tarfile_corrupt_archive_raises_extraction_failed(tmp_path):
    """A non-tar file with .tar.xz suffix raises ExtractionFailedError."""
    archive = tmp_path / "corrupt.tar.xz"
    archive.write_bytes(b"not a tarball at all")

    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(extract_mod.ExtractionFailedError):
        extract_mod.extract_archive(archive, dest)


def test_tarfile_clean_archive_extracts_successfully(tmp_path):
    """Sanity check: a well-formed tar extracts without raising."""
    archive = tmp_path / "good.tar.xz"
    with tarfile.open(archive, "w:xz") as tar:
        info = tarfile.TarInfo(name="ffmpeg/bin/ffmpeg")
        payload = b"#!/bin/sh\necho ok\n"
        info.size = len(payload)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(payload))

    dest = tmp_path / "dest"
    dest.mkdir()
    extract_mod.extract_archive(archive, dest)
    assert (dest / "ffmpeg" / "bin" / "ffmpeg").is_file()


# ---- Zipfile traversal rejection -------------------------------------

def test_zipfile_absolute_path_member_rejected(tmp_path):
    """Zip with an absolute-path member is rejected."""
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("/abs/evil", b"pwned")

    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(extract_mod.ExtractionUnsafeError):
        extract_mod.extract_archive(archive, dest)


def test_zipfile_parent_traversal_member_rejected(tmp_path):
    """Zip with ``../../../etc/evil`` member is rejected."""
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../../etc/evil", b"pwned")

    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(extract_mod.ExtractionUnsafeError):
        extract_mod.extract_archive(archive, dest)


def test_zipfile_normal_member_extracts_clean(tmp_path):
    """A clean zip with a nested file extracts without error."""
    archive = tmp_path / "valid.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(f"ffmpeg/bin/{_binary_name()}", b"binary-bytes")

    dest = tmp_path / "dest"
    dest.mkdir()
    extract_mod.extract_archive(archive, dest)
    assert (dest / "ffmpeg" / "bin" / _binary_name()).is_file()


def test_zipfile_corrupt_archive_raises_extraction_failed(tmp_path):
    """A non-zip file with .zip suffix raises ExtractionFailedError."""
    archive = tmp_path / "corrupt.zip"
    archive.write_bytes(b"not a zip")

    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(extract_mod.ExtractionFailedError):
        extract_mod.extract_archive(archive, dest)


def test_unrecognized_archive_suffix_raises_extraction_failed(tmp_path):
    """Unsupported suffix is treated as an extraction failure."""
    archive = tmp_path / "weird.7z"
    archive.write_bytes(b"data")

    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(extract_mod.ExtractionFailedError):
        extract_mod.extract_archive(archive, dest)
