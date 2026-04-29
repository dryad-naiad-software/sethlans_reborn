# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""FFmpeg download worker thread (FR-M2-7).

Extracted from ``handlers/ffmpeg.py`` to keep that handler under the
project's 300-line ceiling. Behaviour is unchanged — this module owns
the long-running download pipeline (download → SHA verify → extract →
binary detect → ``ffmpeg -version`` post-check); the handler module
owns the WSGI surface and the per-process task registry.

The worker takes a *progress callback* injected by the handler that
the worker uses to update the task registry under the handler's lock.
That keeps ``handlers/ffmpeg.py`` as the single owner of the lock
without coupling this module to a global.
"""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path
from typing import Callable, Optional

import requests

from wizard.sethlans_wizard import ffmpeg_download as ffdl, progress, wizard_state
from wizard.sethlans_wizard.checkpoints import FFMPEG_INSTALLED

logger = logging.getLogger(__name__)


# ``set_status`` callable signature: (task_id, *, status=None,
# percent=None, category=None, error=None) -> None.
SetStatus = Callable[..., None]


def _stage_download(
    task_id: str, url: str, archive_path: Path,
    cancel: threading.Event, set_status: SetStatus,
) -> bool:
    """Run the download. Returns True iff the archive is on disk."""
    set_status(task_id, status="downloading", percent=0)

    def on_percent(pct: int) -> None:
        set_status(task_id, percent=pct)

    try:
        ok = ffdl._stream_download(
            url, archive_path, cancel, on_percent=on_percent,
        )
    except requests.RequestException as exc:
        logger.error("ffmpeg download network error: %s", exc)
        _cleanup(archive_path)
        set_status(
            task_id, status="failed", category="network_error",
            error="network error during download",
        )
        return False
    if not ok:
        _cleanup(archive_path)
        set_status(
            task_id, status="failed", category="download_failed",
            error="download cancelled",
        )
        return False
    return True


def _stage_verify_sha(
    task_id: str, archive_path: Path, platform_id: str,
    set_status: SetStatus,
) -> bool:
    set_status(task_id, status="verifying", percent=99)
    sha_failure = ffdl._verify_sha256(archive_path, platform_id)
    if sha_failure is not None:
        _cleanup(archive_path)
        set_status(
            task_id, status="failed", category=sha_failure,
            error="FFmpeg download verification failed; check for "
                  "Sethlans updates.",
        )
        return False
    return True


def _stage_extract(
    task_id: str, archive_path: Path, dest_dir: Path,
    set_status: SetStatus,
) -> bool:
    set_status(task_id, status="extracting", percent=99)
    try:
        ffdl._extract_archive(archive_path, dest_dir)
    except (ValueError, OSError, Exception) as exc:  # noqa: BLE001
        logger.error("ffmpeg extraction failed: %s", exc)
        _cleanup(archive_path)
        set_status(
            task_id, status="failed", category="extraction_failed",
            error="archive extraction failed",
        )
        return False
    if archive_path.exists():
        _cleanup(archive_path)
    return True


def _stage_version_check(
    task_id: str, data_dir: Path, dest_dir: Path,
    set_status: SetStatus,
) -> Optional[Path]:
    """Locate + run the binary; returns the binary path or None."""
    binary: Optional[Path] = ffdl.get_ffmpeg_binary(data_dir)
    if binary is None:
        set_status(
            task_id, status="failed", category="extraction_failed",
            error="binary not found post-extract",
        )
        return None
    ok, version_str = ffdl.run_version_check(binary)
    if not ok or ffdl.FFMPEG_VERSION not in version_str:
        # FR-M2-7b — kill already handled; clean up so next retry works.
        try:
            shutil.rmtree(dest_dir, ignore_errors=True)
        except OSError:
            pass
        set_status(
            task_id, status="failed", category="version_mismatch",
            error="ffmpeg -version did not match expected pin",
        )
        return None
    return binary


def download_worker(
    task_id: str,
    data_dir: Path,
    cancel: threading.Event,
    set_status: SetStatus,
) -> None:
    """FR-M2-7 happy path + error branches.

    Stages: download → SHA verify → extract → version check. Each
    stage updates *set_status* on transition and returns False/None to
    short-circuit on failure.
    """
    platform_id = ffdl._get_platform_id()
    if platform_id is None or platform_id not in ffdl._FFMPEG_URLS:
        set_status(
            task_id, status="failed", category="download_failed",
            error="unsupported platform",
        )
        return

    url = ffdl._FFMPEG_URLS[platform_id]
    dest_dir = ffdl.get_ffmpeg_dir(data_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    archive_path = dest_dir / url.rsplit("/", 1)[-1]

    if not _stage_download(task_id, url, archive_path, cancel, set_status):
        return
    if not _stage_verify_sha(task_id, archive_path, platform_id, set_status):
        return
    if not _stage_extract(task_id, archive_path, dest_dir, set_status):
        return
    binary = _stage_version_check(task_id, data_dir, dest_dir, set_status)
    if binary is None:
        return

    wizard_state.set_ffmpeg(ffdl.FFMPEG_VERSION, str(binary))
    set_status(task_id, status="complete", percent=100)
    progress.append_checkpoint(data_dir, FFMPEG_INSTALLED)


def _cleanup(archive_path: Path) -> None:
    try:
        if archive_path.exists():
            archive_path.unlink()
    except OSError:
        pass


__all__ = ["download_worker"]
