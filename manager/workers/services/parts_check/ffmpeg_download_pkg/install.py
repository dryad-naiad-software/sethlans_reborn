# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Atomic FFmpeg install pipeline.

Orchestrates the per-spec Tech §220-230 sequence:

    0. Reject if any pinned SHA-256 constant is the placeholder /
       empty / None  ->  failed("placeholder_sha")
    1. HTTPS GET into ``<final_dir>.tmp``
       ->  failed("download_failed") on transport/TLS/HTTP failure
    2. SHA-256 verify against pinned constant
       ->  failed("checksum_mismatch") on mismatch
    3. Validate every archive member (tar 'data' filter, zip member walk)
       ->  failed("extraction_unsafe") on traversal violation
    4. Extract to ``<final_dir>.partial``
       ->  failed("extraction_failed") on generic extraction error
    5. POSIX-only: chmod 0o755 on the binary inside the .partial dir
    6. verify_runs against the binary inside the .partial dir
       ->  failed("verify_runs_failed") on subprocess error
    7. os.replace('<final_dir>.partial', '<final_dir>')
    8. Delete '<final_dir>.tmp'
    9. Return ready(source="bundled", ...)

On every failure path, both ``<final_dir>.tmp`` and
``<final_dir>.partial`` are cleaned up before the function returns.
"""

from __future__ import annotations

import logging
import os
import platform
from pathlib import Path
from typing import Optional, Tuple

from ..registry import Status
from .constants import (
    FFMPEG_SHA256, FFMPEG_URLS, FFMPEG_VERSION,
    get_platform_id, is_placeholder,
)
from .cleanup import cleanup_paths
from .download import (
    ChecksumMismatchError, DownloadFailedError,
    stream_download, verify_sha256,
)
from .extract import (
    ExtractionFailedError, ExtractionUnsafeError, extract_archive,
)
from .locate import get_ffmpeg_binary
from .verify import verify_runs

logger = logging.getLogger(__name__)


def _failed(error: str) -> Status:
    return Status(status="failed", error=error)


def _ready(path: str) -> Status:
    return Status(
        status="ready",
        source="bundled",
        version=FFMPEG_VERSION,
        path=path,
    )


def _archive_suffix(url: str) -> str:
    """Return the archive suffix the extractor will dispatch on."""
    if url.endswith(".tar.xz"):
        return ".tar.xz"
    if url.endswith(".zip"):
        return ".zip"
    return ""


def _resolve_paths(install_dir: Path) -> Tuple[Path, Path, str, str]:
    """Return (final_dir, tmp_archive_path, url, expected_sha).

    ``tmp_archive_path`` is a sibling *file* of ``install_dir`` whose
    name begins with ``<install_dir>.tmp`` so a ``*.tmp`` glob still
    catches it during stale-partials cleanup, but whose suffix matches
    the upstream archive type so ``extract_archive`` can dispatch.
    """
    pid = get_platform_id()
    if pid is None:
        return install_dir, install_dir, "", ""
    url = FFMPEG_URLS.get(pid, "")
    sha = FFMPEG_SHA256.get(pid, "")
    tmp_archive = install_dir.with_name(
        install_dir.name + ".tmp" + _archive_suffix(url),
    )
    return install_dir, tmp_archive, url, sha


def _check_placeholders() -> bool:
    """Return True if every pinned SHA-256 constant is populated."""
    for pid, sha in FFMPEG_SHA256.items():
        if is_placeholder(sha):
            logger.error(
                "FFMPEG_SHA256[%r] is placeholder/empty/None — "
                "failing closed.", pid,
            )
            return False
    return True


def _make_chmod_755(binary_path: Path) -> None:
    """POSIX-only: chmod 0o755 on the located binary.  No-op on Windows."""
    if platform.system() == "Windows":
        return
    try:
        os.chmod(binary_path, 0o755)
    except OSError:
        logger.exception("chmod 0o755 failed on %s", binary_path)


def _download_and_verify(
    url: str, tmp_archive: Path, expected_sha: str, partial_dir: Path,
) -> Optional[str]:
    """Download + SHA-256 verify.  Returns failure error code or None."""
    try:
        stream_download(url, tmp_archive)
    except DownloadFailedError:
        logger.exception("FFmpeg download failed: %s", url)
        cleanup_paths(tmp_archive, partial_dir)
        return "download_failed"
    try:
        verify_sha256(tmp_archive, expected_sha)
    except ChecksumMismatchError:
        cleanup_paths(tmp_archive, partial_dir)
        return "checksum_mismatch"
    return None


def _extract_and_verify(
    tmp_archive: Path, partial_dir: Path,
) -> Optional[str]:
    """Extract + chmod + verify_runs.  Returns failure error code or None."""
    try:
        partial_dir.mkdir(parents=True, exist_ok=True)
        extract_archive(tmp_archive, partial_dir)
    except ExtractionUnsafeError:
        logger.exception("FFmpeg archive contained unsafe member")
        cleanup_paths(tmp_archive, partial_dir)
        return "extraction_unsafe"
    except ExtractionFailedError:
        logger.exception("FFmpeg archive extraction failed")
        cleanup_paths(tmp_archive, partial_dir)
        return "extraction_failed"
    except Exception:
        logger.exception("Unexpected error during FFmpeg extraction")
        cleanup_paths(tmp_archive, partial_dir)
        return "extraction_failed"

    binary = get_ffmpeg_binary(partial_dir)
    if binary is None:
        logger.error(
            "FFmpeg binary not found inside %s after extraction",
            partial_dir,
        )
        cleanup_paths(tmp_archive, partial_dir)
        return "verify_runs_failed"
    _make_chmod_755(binary)
    if not verify_runs(binary):
        cleanup_paths(tmp_archive, partial_dir)
        return "verify_runs_failed"
    return None


def _promote_atomic(
    tmp_archive: Path, partial_dir: Path, final_dir: Path,
) -> Optional[str]:
    """Atomic promote .partial -> final.  Returns failure code or None."""
    try:
        if final_dir.exists():
            cleanup_paths(final_dir)
        os.replace(str(partial_dir), str(final_dir))
    except OSError:
        logger.exception(
            "FFmpeg atomic promote failed: %s -> %s",
            partial_dir, final_dir,
        )
        cleanup_paths(tmp_archive, partial_dir)
        return "extraction_failed"
    cleanup_paths(tmp_archive)
    return None


def download_and_install_bundled(install_dir: Path) -> Status:
    """Run the atomic download+extract+verify pipeline for FFmpeg.

    ``install_dir`` is e.g. ``<data_dir>/bin/ffmpeg/8.1`` — the final
    promoted location.  The function never mutates this path in place;
    it stages everything in sibling ``.tmp`` / ``.partial`` paths and
    promotes via ``os.replace`` only on full success.
    """
    if not _check_placeholders():
        return _failed("placeholder_sha")

    if get_platform_id() is None:
        logger.error(
            "FFmpeg download: unsupported platform %s/%s",
            platform.system(), platform.machine(),
        )
        return _failed("download_failed")

    final_dir, tmp_archive, url, expected_sha = _resolve_paths(install_dir)
    partial_dir = final_dir.with_name(final_dir.name + ".partial")

    final_dir.parent.mkdir(parents=True, exist_ok=True)
    cleanup_paths(tmp_archive, partial_dir)

    err = _download_and_verify(url, tmp_archive, expected_sha, partial_dir)
    if err:
        return _failed(err)

    err = _extract_and_verify(tmp_archive, partial_dir)
    if err:
        return _failed(err)

    err = _promote_atomic(tmp_archive, partial_dir, final_dir)
    if err:
        return _failed(err)

    final_binary = get_ffmpeg_binary(final_dir)
    if final_binary is None:
        logger.error(
            "FFmpeg binary missing after atomic promote at %s",
            final_dir,
        )
        return _failed("verify_runs_failed")
    return _ready(str(final_binary))
