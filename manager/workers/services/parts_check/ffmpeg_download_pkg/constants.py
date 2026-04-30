# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Pinned FFmpeg version, download URLs, and SHA-256 digests.

These constants are the single source of truth for which FFmpeg build
the manager bundles.  Updating means changing **all** of:

    FFMPEG_VERSION            (the major.minor pin)
    FFMPEG_URLS[platform_id]  (the exact upstream URL)
    FFMPEG_SHA256[platform_id] (the SHA-256 of that URL's payload)

There is no per-job version selection.  There is no runtime fallback
to an older version on hash mismatch.  A mismatch is a hard failure;
re-pinning is a release-process action, not a runtime decision.

Sources (carried forward from spec FR §40-44):
    - BtbN/FFmpeg-Builds (Windows, Linux x86_64, Linux ARM64)
    - evermeet.cx (macOS x86_64 and ARM64 — same universal-2 build)

The bundled build is the **GPL** FFmpeg with libx264 / libx265 /
libvpx / prores_ks.  License-compatible with our GPL-2.0-or-later
project.
"""

from __future__ import annotations

import platform
from typing import Optional


# Pinned FFmpeg version.  Bumping this constant means bumping the
# checksum constants below in lock-step.
FFMPEG_VERSION = "8.1"


# The literal sentinel that v1 of this module accepted.  v2+ rejects
# it: any constant equal to this string, or empty, or None, makes the
# parts-check fail with error="placeholder_sha".
PLACEHOLDER_SENTINEL = "PLACEHOLDER_FILL_AT_RELEASE"


# ---- Download URLs (FR §40-44) -------------------------------------
#
# BtbN auto-build "latest" tag holds versioned assets for the most
# recent two upstream releases.  We pin to the n8.1 GPL static build.
#
# evermeet.cx is single-maintainer; the documented fallback is to
# mirror to a project-owned GitHub release.  Mirror migration is a
# release-process action, never a runtime fallback.

FFMPEG_URLS: dict[str, str] = {
    "windows-x64": (
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
        "latest/ffmpeg-n8.1-latest-win64-gpl-8.1.zip"
    ),
    "linux-x64": (
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
        "latest/ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz"
    ),
    "linux-arm64": (
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
        "latest/ffmpeg-n8.1-latest-linuxarm64-gpl-8.1.tar.xz"
    ),
    "macos-arm64": "https://evermeet.cx/ffmpeg/ffmpeg-8.1.zip",
    "macos-x64": "https://evermeet.cx/ffmpeg/ffmpeg-8.1.zip",
}


# ---- Pinned SHA-256 digests ----------------------------------------
#
# Each digest corresponds to the upstream URL above with the same key.
# Computed 2026-04-30 by:
#
#   curl -sL "<URL>" -o /tmp/x && sha256sum /tmp/x
#
# The Linux/Windows digests were also cross-verified against
# https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/checksums.sha256
#
# A mismatch at runtime is a hard failure (error="checksum_mismatch").
# An empty string / None / PLACEHOLDER_SENTINEL value is a hard failure
# at boot (error="placeholder_sha").

FFMPEG_SHA256: dict[str, str] = {
    # https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-win64-gpl-8.1.zip
    "windows-x64":
        "3f513a4ac4bc9493e81ba18cc0d752da8a4fbf4ae098372dc576c26aafe81ba9",
    # https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz
    "linux-x64":
        "cadf9d157e199a132e9e032d6e7d1c94fbae3f1caa5885bc7595ea06b55f56ed",
    # https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n8.1-latest-linuxarm64-gpl-8.1.tar.xz
    "linux-arm64":
        "db3bd15b5a32e54ddefe9830b82247f3f0b068aca6f399ff6815754c8748d9ea",
    # https://evermeet.cx/ffmpeg/ffmpeg-8.1.zip — universal-2 (x86_64 + arm64)
    "macos-arm64":
        "d67db25908eff64b7d0eaa73784f0c55728d9e036a96931095fcf8e8968eefab",
    "macos-x64":
        "d67db25908eff64b7d0eaa73784f0c55728d9e036a96931095fcf8e8968eefab",
}


def get_platform_id() -> Optional[str]:
    """Return the platform identifier matching FFMPEG_URLS keys.

    Returns ``None`` for unsupported platform/arch combinations; the
    caller fails closed with error="download_failed" (transport-bucket
    catch-all) since there is no archive to fetch.
    """
    system = platform.system().lower()
    arch = platform.machine().lower()
    if system == "windows":
        return "windows-x64"
    if system == "linux":
        if arch in ("aarch64", "arm64"):
            return "linux-arm64"
        return "linux-x64"
    if system == "darwin":
        if "arm" in arch or "aarch64" in arch:
            return "macos-arm64"
        return "macos-x64"
    return None


def is_placeholder(digest: Optional[str]) -> bool:
    """Return True if ``digest`` is missing, empty, or the sentinel."""
    if digest is None:
        return True
    if not digest:
        return True
    return digest == PLACEHOLDER_SENTINEL


def ffmpeg_binary_name() -> str:
    """Return the platform-specific FFmpeg binary file name."""
    return "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg"
