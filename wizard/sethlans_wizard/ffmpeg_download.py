# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""FFmpeg download / extract / verify for the wizard (FR-M2-7).

Ported from ``manager/workers/services/ffmpeg_download.py``. Reuses
``worker/sethlans_worker_agent/utils/file_operations.py`` for safe
archive extraction (``_safe_zip_extract`` for zips, ``tarfile`` with
``filter='data'`` for tarballs) — DO NOT re-implement extraction.

Per FR-M2-7 + FR-M2-7a + FR-M2-7b:

* Per-platform download URLs are hardcoded constants pinned to the
  upstream BtbN / evermeet.cx artifacts.
* Per-platform SHA-256 expected values live in
  :data:`EXPECTED_FFMPEG_SHA256` keyed by ``(system, arch)``. The
  hashes are populated to the actual upstream artifact digests at
  release-cut time (Phase 1 ships the structure with placeholder
  hashes — see Phase 1 progress notes; the verify path short-circuits
  when the placeholder marker is present).
* All ``subprocess.run`` calls use ``shell=False`` and list-form args.
* Stderr from ``ffmpeg -version`` is NOT surfaced verbatim to the
  user — the user-facing string is fixed.

This module exposes the *download orchestration* surface; the WSGI
handler (``handlers/ffmpeg.py``) owns the task-registry lock + the
single-task invariant.
"""

from __future__ import annotations

import hashlib
import logging
import platform
import subprocess
import tarfile
import threading
from pathlib import Path
from typing import Optional

import requests

from worker.sethlans_worker_agent.utils.file_operations import (
    _safe_zip_extract,
)

logger = logging.getLogger(__name__)

# Pinned FFmpeg version. Bumped together with the SHA-256 dict below.
FFMPEG_VERSION = "7.1"

# Connect / read tuple — slow-drip downloads need a generous read phase.
HTTP_TIMEOUT = (10, 120)
SUBPROCESS_TIMEOUT_SECONDS = 5

# Sentinel value indicating the SHA pin has not yet been populated for
# this build. When a key in :data:`EXPECTED_FFMPEG_SHA256` matches this
# value, the verify path skips SHA comparison and logs a WARNING so the
# operator notices an unsigned build pre-release. The release pipeline
# MUST populate every entry before shipping.
SHA256_PLACEHOLDER = "PLACEHOLDER_FILL_AT_RELEASE"

# (system, arch) → SHA-256 hex string of the upstream archive. Keys
# match the values returned by :func:`_get_platform_id`.
EXPECTED_FFMPEG_SHA256: dict[str, str] = {
    "windows-x64": SHA256_PLACEHOLDER,
    "linux-x64": SHA256_PLACEHOLDER,
    "linux-arm64": SHA256_PLACEHOLDER,
    "macos-arm64": SHA256_PLACEHOLDER,
    "macos-x64": SHA256_PLACEHOLDER,
}

# Hardcoded download URLs (FR-M2-7). Mirror manager-side ffmpeg_download
# until that module is deleted in Phase 4.
_FFMPEG_URLS: dict[str, str] = {
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
    "macos-arm64": "https://evermeet.cx/ffmpeg/ffmpeg-7.1.1.zip",
    "macos-x64": "https://evermeet.cx/ffmpeg/ffmpeg-7.1.1.zip",
}


def _get_platform_id() -> Optional[str]:
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
    """Return the per-data-dir FFmpeg install directory."""
    return data_dir / "bin" / "ffmpeg" / FFMPEG_VERSION


def get_ffmpeg_binary(data_dir: Path) -> Optional[Path]:
    """Return the path to the ffmpeg binary if installed, else None."""
    ffmpeg_dir = get_ffmpeg_dir(data_dir)
    if platform.system() == "Windows":
        candidates = list(ffmpeg_dir.rglob("ffmpeg.exe"))
    else:
        candidates = list(ffmpeg_dir.rglob("ffmpeg"))
    for c in candidates:
        if c.is_file():
            return c
    return None


def already_installed(data_dir: Path) -> bool:
    """True if a binary is already present (idempotent short-circuit)."""
    return get_ffmpeg_binary(data_dir) is not None


def _hash_file(path: Path) -> str:
    """Return the SHA-256 hex digest of *path*."""
    hasher = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _stream_download(
    url: str,
    archive_path: Path,
    cancel: threading.Event,
    on_percent: Optional[callable] = None,
) -> bool:
    """Stream-download *url* to *archive_path*.

    Returns True on success, False if *cancel* fires mid-transfer.
    """
    resp = requests.get(url, stream=True, verify=True, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    with open(archive_path, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=65536):
            if cancel.is_set():
                return False
            fh.write(chunk)
            downloaded += len(chunk)
            if on_percent is not None and total:
                pct = int(downloaded * 100 / total)
                on_percent(min(pct, 99))
    return True


def _verify_sha256(archive_path: Path, platform_id: str) -> Optional[str]:
    """Check *archive_path* against :data:`EXPECTED_FFMPEG_SHA256`.

    Returns ``None`` on success, or an error category string on
    failure (``sha_mismatch``).
    """
    expected = EXPECTED_FFMPEG_SHA256.get(platform_id)
    if expected is None or expected == SHA256_PLACEHOLDER:
        logger.warning(
            "FFmpeg SHA-256 pin missing/placeholder for %s; skipping verify",
            platform_id,
        )
        return None
    actual = _hash_file(archive_path)
    if actual != expected:
        logger.error(
            "FFmpeg SHA mismatch for %s: expected %s got %s",
            platform_id, expected, actual,
        )
        return "sha_mismatch"
    return None


def _extract_archive(archive_path: Path, dest_dir: Path) -> None:
    """Safely extract *archive_path* (.zip or .tar.xz) to *dest_dir*."""
    name = str(archive_path)
    if name.endswith(".tar.xz"):
        resolved = str(dest_dir.resolve())
        with tarfile.open(name, "r:xz") as tar:
            tar.extractall(path=resolved, filter="data")
    elif name.endswith(".zip"):
        _safe_zip_extract(name, str(dest_dir))
    else:
        raise ValueError(f"Unsupported archive extension: {archive_path}")


def run_version_check(
    binary_path: Path,
    timeout: float = SUBPROCESS_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """Run ``ffmpeg -version`` with a hard timeout (FR-M2-7b).

    Returns ``(success, version_string)``. On ``TimeoutExpired`` the
    child is killed (``Popen.kill``) and ``success=False`` is returned.
    Stderr is NEVER surfaced to the user — the caller maps the boolean
    onto a fixed user-visible message.
    """
    try:
        proc = subprocess.run(
            [str(binary_path), "-version"],
            shell=False,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        logger.error(
            "ffmpeg -version exceeded %ss timeout (binary=%s)",
            timeout, binary_path,
        )
        # subprocess.run already kills on timeout, but TimeoutExpired
        # may be re-raised before kill on some Pythons — be defensive.
        try:  # pragma: no cover - defensive
            if exc and getattr(exc, "process", None) is not None:
                exc.process.kill()
        except Exception:  # noqa: BLE001 — defensive
            pass
        return False, ""
    except OSError as exc:
        logger.error("ffmpeg -version failed to launch: %s", exc)
        return False, ""
    if proc.returncode != 0:
        logger.error(
            "ffmpeg -version returned %s; stderr (logged only): %r",
            proc.returncode, proc.stderr[:200],
        )
        return False, ""
    return True, (proc.stdout or "").strip()


__all__ = [
    "FFMPEG_VERSION",
    "EXPECTED_FFMPEG_SHA256",
    "SHA256_PLACEHOLDER",
    "_get_platform_id",
    "get_ffmpeg_dir",
    "get_ffmpeg_binary",
    "already_installed",
    "_stream_download",
    "_verify_sha256",
    "_extract_archive",
    "run_version_check",
]
