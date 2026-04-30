# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Traversal-safe archive extraction.

Two formats only — matching what the upstream sources publish:

    .tar.xz  — Linux (BtbN)
    .zip     — Windows (BtbN) and macOS (evermeet.cx)

Both branches validate every member **before** the bytes hit disk:

- tarfiles use Python 3.12+ ``filter='data'`` which rejects absolute
  paths, ``..`` segments, links pointing outside the destination, and
  special-file types.
- zipfiles iterate ``namelist()`` and reject any member where:
    * ``startswith('/')``
    * the path is absolute (``Path(...).is_absolute()``)
    * the path contains a ``..`` segment
    * the resolved (``os.path.realpath``) target falls outside the
      destination directory.

Any traversal violation aborts extraction and raises
``ExtractionUnsafeError``.  Generic extraction failures (corrupt
archive, IO error during write) raise ``ExtractionFailedError``.

The caller (``install.py``) maps these two exception types to the
closed-vocabulary ``error`` strings ``"extraction_unsafe"`` and
``"extraction_failed"`` respectively.
"""

from __future__ import annotations

import logging
import os
import tarfile
import zipfile
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)


class ExtractionUnsafeError(Exception):
    """Raised when an archive member would escape the destination dir."""


class ExtractionFailedError(Exception):
    """Raised when extraction fails for a non-traversal reason."""


def _validate_zip_member(member_name: str, dest: Path) -> None:
    """Reject any zip member that would escape ``dest``.

    Raises ``ExtractionUnsafeError`` on any of the four traversal
    fingerprints.
    """
    if member_name.startswith("/"):
        raise ExtractionUnsafeError(
            f"absolute path member: {member_name!r}",
        )
    member_path = Path(member_name)
    if member_path.is_absolute():
        raise ExtractionUnsafeError(
            f"absolute path member: {member_name!r}",
        )
    if ".." in member_path.parts:
        raise ExtractionUnsafeError(
            f"parent traversal member: {member_name!r}",
        )
    target = os.path.realpath(os.path.join(str(dest), member_name))
    dest_real = os.path.realpath(str(dest))
    # On POSIX the simple prefix check is correct after realpath; on
    # Windows we still need the case-insensitive comparator since file
    # systems are case-insensitive but realpath does not normalize case.
    target_norm = os.path.normcase(target)
    dest_norm = os.path.normcase(dest_real)
    if not (
        target_norm == dest_norm
        or target_norm.startswith(dest_norm + os.sep)
    ):
        raise ExtractionUnsafeError(
            f"member resolves outside destination: {member_name!r}",
        )


def extract_archive(
    archive_path: Union[str, Path],
    dest_dir: Union[str, Path],
) -> None:
    """Extract ``archive_path`` into ``dest_dir`` with traversal checks.

    ``dest_dir`` must already exist and be a directory.  The archive
    type is detected from the file suffix (``.tar.xz`` or ``.zip``).

    Raises:
        ExtractionUnsafeError — traversal violation; caller maps to
            ``error="extraction_unsafe"``.
        ExtractionFailedError — generic extraction error; caller maps
            to ``error="extraction_failed"``.
    """
    archive_str = str(archive_path)
    dest_path = Path(dest_dir)

    if archive_str.endswith(".tar.xz"):
        _extract_tar_xz(archive_str, dest_path)
    elif archive_str.endswith(".zip"):
        _extract_zip(archive_str, dest_path)
    else:
        raise ExtractionFailedError(
            f"unrecognized archive suffix: {archive_str!r}",
        )


def _extract_tar_xz(archive_str: str, dest_path: Path) -> None:
    """Extract an .tar.xz with Python 3.12+ data filter."""
    try:
        with tarfile.open(archive_str, "r:xz") as tar:
            try:
                tar.extractall(path=str(dest_path), filter="data")
            except tarfile.AbsoluteLinkError as exc:
                raise ExtractionUnsafeError(
                    "tar member with absolute link",
                ) from exc
            except tarfile.OutsideDestinationError as exc:
                raise ExtractionUnsafeError(
                    "tar member resolves outside destination",
                ) from exc
            except tarfile.LinkOutsideDestinationError as exc:
                raise ExtractionUnsafeError(
                    "tar link member resolves outside destination",
                ) from exc
            except tarfile.FilterError as exc:
                # Catch-all for any other 3.12+ filter rejection.
                raise ExtractionUnsafeError(
                    "tar member rejected by 'data' filter",
                ) from exc
    except (tarfile.TarError, OSError) as exc:
        if isinstance(exc, ExtractionUnsafeError):  # pragma: no cover
            raise
        raise ExtractionFailedError(
            f"tar extraction error: {exc.__class__.__name__}",
        ) from exc


def _extract_zip(archive_str: str, dest_path: Path) -> None:
    """Extract a .zip with member-by-member traversal validation."""
    try:
        with zipfile.ZipFile(archive_str, "r") as zf:
            # Pre-validate every member before any byte hits disk.
            for member_name in zf.namelist():
                _validate_zip_member(member_name, dest_path)
            zf.extractall(path=str(dest_path))
    except ExtractionUnsafeError:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise ExtractionFailedError(
            f"zip extraction error: {exc.__class__.__name__}",
        ) from exc
