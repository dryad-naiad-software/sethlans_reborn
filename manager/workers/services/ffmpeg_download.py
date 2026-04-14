# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
FFmpeg download, extraction, and verification for the setup wizard.

Downloads platform-appropriate FFmpeg binaries from hardcoded trusted
sources.  Progress is reported via the ``download_progress`` module so
the REST polling endpoint can serve it.

Sources (FR-FF1):
  - Windows/Linux: BtbN/FFmpeg-Builds on GitHub
  - macOS: evermeet.cx

All HTTP requests use ``verify=True``.
"""

import logging
import pathlib
import platform
import tarfile
import threading
import zipfile
from pathlib import Path

import requests

from .download_progress import update_task

logger = logging.getLogger(__name__)

# Pinned FFmpeg version
FFMPEG_VERSION = "7.1"

# ---- Platform-specific download URLs (hardcoded, FR-FF1) ----
_FFMPEG_URLS = {
    "windows-x64": (
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
        "latest/ffmpeg-n7.1-latest-win64-gpl-7.1.zip"
    ),
    "linux-x64": (
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
        "latest/ffmpeg-n7.1-latest-linux64-gpl-7.1.tar.xz"
    ),
    "linux-arm64": (
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
        "latest/ffmpeg-n7.1-latest-linuxarm64-gpl-7.1.tar.xz"
    ),
    "macos-arm64": (
        "https://evermeet.cx/ffmpeg/ffmpeg-7.1.1.zip"
    ),
    "macos-x64": (
        "https://evermeet.cx/ffmpeg/ffmpeg-7.1.1.zip"
    ),
}


def _get_platform_id() -> str | None:
    """Return platform identifier matching the download URL keys."""
    system = platform.system().lower()
    arch = platform.machine().lower()
    if system == "windows":
        return "windows-x64"
    if system == "linux":
        if arch == "aarch64":
            return "linux-arm64"
        return "linux-x64"
    if system == "darwin":
        if "arm" in arch or "aarch64" in arch:
            return "macos-arm64"
        return "macos-x64"
    return None


def get_ffmpeg_dir(data_dir: Path) -> Path:
    """Return the FFmpeg installation directory."""
    return data_dir / "bin" / "ffmpeg" / FFMPEG_VERSION


def get_ffmpeg_binary(data_dir: Path) -> Path | None:
    """Return path to the ffmpeg binary if installed, else None."""
    ffmpeg_dir = get_ffmpeg_dir(data_dir)
    if platform.system() == "Windows":
        candidates = list(ffmpeg_dir.rglob("ffmpeg.exe"))
    else:
        candidates = list(ffmpeg_dir.rglob("ffmpeg"))
    for c in candidates:
        if c.is_file():
            return c
    return None


def ffmpeg_already_installed(data_dir: Path) -> bool:
    """Return True if FFmpeg is already present (FR-FF5)."""
    return get_ffmpeg_binary(data_dir) is not None


def _safe_zip_extract(archive_path: str, extract_to: str) -> None:
    """Zip extraction with path traversal protection."""
    target = pathlib.Path(extract_to).resolve()
    with zipfile.ZipFile(archive_path, "r") as zf:
        for member in zf.namelist():
            member_path = (target / member).resolve()
            if not member_path.is_relative_to(target):
                raise ValueError(
                    f"Zip contains path traversal: {member}"
                )
        zf.extractall(path=str(target))


def _stream_download(
    url: str,
    archive_path: Path,
    task_id: str,
    cancel: threading.Event,
) -> bool:
    """Download a file with progress reporting.  Returns True on success."""
    update_task(task_id, status="downloading", percent=0)
    resp = requests.get(url, stream=True, verify=True, timeout=60)
    resp.raise_for_status()

    total = int(resp.headers.get("content-length", 0))
    downloaded = 0

    with open(archive_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            if cancel.is_set():
                return False
            f.write(chunk)
            downloaded += len(chunk)
            pct = int(downloaded * 100 / total) if total else 0
            update_task(
                task_id, status="downloading", percent=min(pct, 99),
            )
    return True


def _extract_archive(archive_path: Path, dest_dir: Path) -> None:
    """Extract a .tar.xz or .zip archive safely."""
    name = str(archive_path)
    if name.endswith(".tar.xz"):
        resolved = str(dest_dir.resolve())
        with tarfile.open(name, "r:xz") as tar:
            tar.extractall(path=resolved, filter="data")
    elif name.endswith(".zip"):
        _safe_zip_extract(name, str(dest_dir))


def download_ffmpeg(task_id: str, data_dir: Path) -> None:
    """Download and install FFmpeg in a background thread."""
    from .download_progress import get_task

    pid = _get_platform_id()
    if not pid or pid not in _FFMPEG_URLS:
        update_task(
            task_id, status="failed",
            error=f"Unsupported platform: {platform.system()} "
                  f"{platform.machine()}",
        )
        return

    url = _FFMPEG_URLS[pid]
    dest_dir = get_ffmpeg_dir(data_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    archive_path = dest_dir / url.rsplit("/", 1)[-1]

    progress = get_task(task_id)
    if progress is None:
        return
    cancel = progress.cancel_event

    try:
        if not _stream_download(url, archive_path, task_id, cancel):
            _cleanup_partial(archive_path, dest_dir)
            update_task(task_id, status="failed", error="Cancelled")
            return

        update_task(task_id, status="extracting", percent=0)
        if cancel.is_set():
            _cleanup_partial(archive_path, dest_dir)
            update_task(task_id, status="failed", error="Cancelled")
            return

        _extract_archive(archive_path, dest_dir)
        if archive_path.exists():
            archive_path.unlink()

        update_task(task_id, status="verifying", percent=0)
        binary = get_ffmpeg_binary(data_dir)
        if binary is None:
            update_task(
                task_id, status="failed",
                error="FFmpeg binary not found after extraction",
            )
            return

        update_task(task_id, status="complete", percent=100)
        logger.info("FFmpeg downloaded to %s", binary)

    except requests.RequestException as exc:
        _cleanup_partial(archive_path, dest_dir)
        update_task(
            task_id, status="failed", error=f"Download error: {exc}",
        )
    except Exception as exc:
        _cleanup_partial(archive_path, dest_dir)
        update_task(
            task_id, status="failed",
            error=f"Unexpected error: {exc}",
        )
        logger.exception("FFmpeg download failed")


def _cleanup_partial(archive_path: Path, dest_dir: Path) -> None:
    """Remove partial download artifacts (FR-FF4)."""
    try:
        if archive_path.exists():
            archive_path.unlink()
    except OSError:
        pass


def start_ffmpeg_download(
    task_id: str, data_dir: Path,
) -> threading.Thread:
    """Launch the download in a daemon thread."""
    t = threading.Thread(
        target=download_ffmpeg,
        args=(task_id, data_dir),
        daemon=True,
        name=f"ffmpeg-download-{task_id[:8]}",
    )
    t.start()
    return t
