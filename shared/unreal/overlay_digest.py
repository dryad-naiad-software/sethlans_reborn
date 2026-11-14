# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Overlay directory digest (Unreal foundation FR-MU-5, D3).

An overlay's pin (``Asset.overlay_sha256``) is::

    sha256(json.dumps([[relpath, size, file_sha256], ...],
                      separators=(',', ':'), ensure_ascii=False).encode('utf-8'))

over every regular file under the overlay root, with ``/``-separated
relative paths sorted by Python ``str`` order. Symlinks, junctions and
other reparse points, and anything that is neither a regular file nor a
directory, are refused. The admin computes the pin with
``python -m shared.unreal.overlay_digest <dir>`` (or the manager's
``unreal_fingerprint`` command), and the worker checks its staged copy
with this same function, so the two cannot drift.
"""
import hashlib
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

_CHUNK = 8 * 1024 * 1024
# Deepest directory nesting walked below the overlay root.
MAX_DEPTH = 64
# ``FILE_ATTRIBUTE_REPARSE_POINT``; ``st_file_attributes`` exists on Windows only.
_REPARSE_POINT = 0x400


class OverlayDigestError(ValueError):
    """The overlay tree contains something that cannot be pinned."""


@dataclass(frozen=True)
class OverlayDigest:
    """The digest and the ``(relpath, size, sha256)`` entries it covers."""
    digest: str
    files: Tuple[Tuple[str, int, str], ...]


def _lstat_checked(path: str, relpath: str) -> os.stat_result:
    st = os.stat(path, follow_symlinks=False)
    if stat.S_ISLNK(st.st_mode) or getattr(st, 'st_file_attributes', 0) & _REPARSE_POINT:
        raise OverlayDigestError(f"overlay entry {relpath!r} is a link or reparse point")
    if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):
        raise OverlayDigestError(f"overlay entry {relpath!r} is not a regular file or directory")
    return st


def _check_opened(handle, listed: os.stat_result, relpath: str) -> None:
    """The opened file must be the regular file that was listed (no swap)."""
    opened = os.fstat(handle.fileno())
    same = (opened.st_ino, opened.st_dev, opened.st_size) == (listed.st_ino, listed.st_dev, listed.st_size)
    reparse = getattr(opened, 'st_file_attributes', 0) & _REPARSE_POINT
    if not same or reparse or not stat.S_ISREG(opened.st_mode):
        raise OverlayDigestError(f"overlay file {relpath!r} changed between listing and opening")


def _hash_file(path: str, relpath: str, listed: os.stat_result) -> Tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    expected_size = listed.st_size
    with open(path, 'rb') as handle:
        _check_opened(handle, listed, relpath)
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    if size != expected_size:
        raise OverlayDigestError(f"overlay file {relpath!r} changed while it was hashed")
    return size, digest.hexdigest()


def _walk(directory: str, prefix: str, out: list, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise OverlayDigestError(f"overlay is nested deeper than {MAX_DEPTH} directories")
    with os.scandir(directory) as entries:
        for entry in entries:
            relpath = f"{prefix}{entry.name}"
            st = _lstat_checked(entry.path, relpath)
            if stat.S_ISDIR(st.st_mode):
                _walk(entry.path, relpath + '/', out, depth + 1)
            else:
                size, sha = _hash_file(entry.path, relpath, st)
                out.append((relpath, size, sha))


def compute_overlay_digest(root) -> OverlayDigest:
    """Return the digest of the overlay directory ``root``.

    Raises:
        OverlayDigestError: ``root`` or an entry under it is a link,
            reparse point or special file, a file was swapped or changed
            while hashed, or directories nest deeper than ``MAX_DEPTH``.
        OSError: the tree cannot be read.
    """
    root = os.fspath(Path(root))
    st = _lstat_checked(root, '.')
    if not stat.S_ISDIR(st.st_mode):
        raise OverlayDigestError("overlay root is not a directory")
    files: list = []
    _walk(root, '', files)
    files.sort(key=lambda item: item[0])
    payload = json.dumps(
        [[relpath, size, sha] for relpath, size, sha in files],
        separators=(',', ':'), ensure_ascii=False,
    ).encode('utf-8')
    return OverlayDigest(digest=hashlib.sha256(payload).hexdigest(), files=tuple(files))


def main(argv=None) -> int:
    """``python -m shared.unreal.overlay_digest <dir>``: print the digest."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m shared.unreal.overlay_digest <dir>", file=sys.stderr)
        return 2
    try:
        result = compute_overlay_digest(args[0])
    except (OSError, ValueError) as exc:  # OverlayDigestError is a ValueError
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(result.digest)
    return 0


if __name__ == '__main__':  # pragma: no cover
    sys.exit(main())
