# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Blender pre-download for the setup wizard.

Uses the existing ``blender_release_parser`` to discover download URLs
for the default Blender version, then downloads and extracts to the
manager's data directory.  Progress is reported via the shared
``download_progress`` module.

The download pattern mirrors ``worker/sethlans_worker_agent/tool_manager.py``
but runs on the manager side for setup-time pre-download.
"""

import logging
import os
import pathlib
import platform
import stat
import tarfile
import threading
import zipfile
from pathlib import Path

import requests

from .download_progress import get_task, update_task

logger = logging.getLogger(__name__)


def get_platform_id() -> str | None:
    """Return platform identifier for Blender download URLs."""
    system = platform.system().lower()
    arch = platform.machine().lower()
    if system == "windows":
        return "windows-x64" if "64" in arch else None
    if system == "linux":
        if arch == "aarch64":
            return "linux-arm64"
        if arch == "x86_64":
            return "linux-x64"
    if system == "darwin":
        if "arm" in arch or "aarch64" in arch:
            return "macos-arm64"
        return "macos-x64"
    return None


def get_blender_dir(data_dir: Path) -> Path:
    """Return the base directory for managed Blender installations."""
    return data_dir / "bin" / "blender"


def blender_already_installed(
    data_dir: Path, version: str,
) -> bool:
    """Check if a Blender version is already extracted."""
    blender_dir = get_blender_dir(data_dir)
    pid = get_platform_id()
    if not pid:
        return False
    install_name = f"blender-{version}-{pid}"
    install_path = blender_dir / install_name
    if not install_path.is_dir():
        return False
    # Check for the executable
    if platform.system() == "Windows":
        return (install_path / "blender.exe").is_file()
    if platform.system() == "Darwin":
        return (
            install_path / "Blender.app" / "Contents"
            / "MacOS" / "Blender"
        ).is_file()
    return (install_path / "blender").is_file()


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


def _get_release_info(version: str, platform_id: str) -> dict | None:
    """Fetch release info via the blender_release_parser."""
    from workers.utils.blender_release_parser import (
        get_blender_releases,
    )
    releases = get_blender_releases()
    return releases.get(version, {}).get(platform_id)


def _stream_download(
    url: str,
    archive_path: Path,
    task_id: str,
    cancel: threading.Event,
) -> bool:
    """Download a file with progress.  Returns True on success."""
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


def _set_executable(blender_dir: Path, version: str, pid: str) -> None:
    """Set executable permission on POSIX."""
    if platform.system() != "Windows":
        install_name = f"blender-{version}-{pid}"
        exe = blender_dir / install_name / "blender"
        if exe.is_file():
            st = os.stat(exe)
            os.chmod(exe, st.st_mode | stat.S_IEXEC)


def _resolve_release(version: str) -> tuple[str, str, str] | None:
    """Return ``(url, hash, platform_id)`` or None."""
    pid = get_platform_id()
    if not pid:
        return None
    info = _get_release_info(version, pid)
    if not info or not info.get("url"):
        return None
    return info["url"], info.get("sha256", ""), pid


def _run_pipeline(
    task_id: str, data_dir: Path, version: str,
    url: str, expected_hash: str, pid: str,
    cancel: threading.Event,
) -> None:
    """Execute the download-verify-extract pipeline."""
    blender_dir = get_blender_dir(data_dir)
    blender_dir.mkdir(parents=True, exist_ok=True)
    archive_path = blender_dir / url.rsplit("/", 1)[-1]

    try:
        if not _stream_download(url, archive_path, task_id, cancel):
            _cleanup(archive_path)
            update_task(task_id, status="failed", error="Cancelled")
            return

        update_task(task_id, status="verifying", percent=0)
        if not _verify_hash(archive_path, expected_hash):
            _cleanup(archive_path)
            update_task(
                task_id, status="failed",
                error="SHA-256 verification failed",
            )
            return

        update_task(task_id, status="extracting", percent=0)
        if cancel.is_set():
            _cleanup(archive_path)
            update_task(task_id, status="failed", error="Cancelled")
            return

        _extract_archive(archive_path, blender_dir)
        _cleanup(archive_path)
        _set_executable(blender_dir, version, pid)

        if not blender_already_installed(data_dir, version):
            update_task(
                task_id, status="failed",
                error="Blender binary not found after extraction",
            )
            return

        update_task(task_id, status="complete", percent=100)
        logger.info("Blender %s downloaded to %s", version, blender_dir)

    except requests.RequestException as exc:
        _cleanup(archive_path)
        update_task(
            task_id, status="failed", error=f"Download error: {exc}",
        )
    except Exception as exc:
        _cleanup(archive_path)
        update_task(
            task_id, status="failed",
            error=f"Unexpected error: {exc}",
        )
        logger.exception("Blender download failed")


def download_blender(
    task_id: str, data_dir: Path, version: str,
) -> None:
    """Download and install Blender.  Called in a background thread."""
    resolved = _resolve_release(version)
    if not resolved:
        update_task(
            task_id, status="failed",
            error=f"No Blender {version} release for this platform",
        )
        return

    url, expected_hash, pid = resolved
    if not expected_hash:
        update_task(
            task_id, status="failed",
            error="No SHA-256 hash available for verification",
        )
        return

    progress = get_task(task_id)
    if progress is None:
        return

    _run_pipeline(
        task_id, data_dir, version,
        url, expected_hash, pid,
        progress.cancel_event,
    )


def _verify_hash(path: Path, expected: str) -> bool:
    """SHA-256 hash verification."""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest() == expected


def _cleanup(archive_path: Path) -> None:
    """Remove a partial archive file."""
    try:
        if archive_path.exists():
            archive_path.unlink()
    except OSError:
        pass


def start_blender_download(
    task_id: str, data_dir: Path, version: str,
) -> threading.Thread:
    """Launch the download in a daemon thread."""
    t = threading.Thread(
        target=download_blender,
        args=(task_id, data_dir, version),
        daemon=True,
        name=f"blender-download-{task_id[:8]}",
    )
    t.start()
    return t
