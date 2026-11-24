# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Pinned FFmpeg version, download URLs, and SHA-256 digests.

These constants are the single source of truth for which FFmpeg build
the manager bundles.  Updating means changing **all** of:

    FFMPEG_VERSION            (the major.minor pin)
    FFMPEG_URLS[platform_id]  (the exact upstream URL; the BtbN ones
                               are built from BTBN_RELEASE_TAG and
                               BTBN_ASSETS)
    FFMPEG_SHA256[platform_id] (the SHA-256 of that URL's payload)

There is no per-job version selection.  There is no runtime fallback
to an older version on hash mismatch.  A mismatch is a hard failure;
re-pinning is a release-process action, not a runtime decision.

Sources (carried forward from spec FR §40-44):
    - BtbN/FFmpeg-Builds (Windows, Linux x86_64, Linux ARM64), pinned
      to one dated month-end ``autobuild-*`` release (see below)
    - evermeet.cx (macOS x86_64 and ARM64 — same universal-2 build)

Why a dated month-end BtbN release, not ``latest``
--------------------------------------------------
BtbN rebuilds the ``latest`` release every day, so the bytes (and the
SHA-256) behind a ``latest/...`` URL change under a fixed pin and every
new install fails ``checksum_mismatch``.  Each daily build is also
published as a dated ``autobuild-YYYY-MM-DD-HH-MM`` release whose
assets do not change.  BtbN keeps the daily dated releases for about
two weeks, but keeps the month-end ones for about two years, so we pin
a month-end release.

Pins taken 2026-11-23 from ``autobuild-2026-10-31-12-59`` (FFmpeg
n8.1.3).  Digests are GitHub's asset digests, cross-checked against
that release's ``checksums.sha256``.

Re-pinning the BtbN builds
--------------------------
1. Pick a newer month-end release ``autobuild-YYYY-MM-DD-HH-MM`` on
   https://github.com/BtbN/FFmpeg-Builds/releases that still ships an
   ``n<FFMPEG_VERSION>`` GPL build for every BtbN platform below.
2. Set ``BTBN_RELEASE_TAG`` and the three ``BTBN_ASSETS`` names.
3. Take the three digests from that release's ``checksums.sha256``
   and update ``FFMPEG_SHA256``.
4. Run ``tools/check_ffmpeg_pins.py``, which downloads every pinned
   URL and checks its digest.

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
# BtbN: one dated month-end ``autobuild-*`` release (immutable assets,
# kept about two years).  Never ``latest``: it is rebuilt daily.  We
# pin the n8.1 GPL static builds.
#
# evermeet.cx is single-maintainer; the documented fallback is to
# mirror to a project-owned GitHub release.  Mirror migration is a
# release-process action, never a runtime fallback.  The evermeet.cx
# pin (taken 2026-04-30) was re-checked on 2026-11-23 and still matched.

BTBN_RELEASE_TAG = "autobuild-2026-10-31-12-59"

BTBN_DOWNLOAD_BASE = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
    f"{BTBN_RELEASE_TAG}/"
)

BTBN_ASSETS: dict[str, str] = {
    "windows-x64": "ffmpeg-n8.1.3-27-g8dca1586f4-win64-gpl-8.1.zip",
    "linux-x64": "ffmpeg-n8.1.3-27-g8dca1586f4-linux64-gpl-8.1.tar.xz",
    "linux-arm64": "ffmpeg-n8.1.3-27-g8dca1586f4-linuxarm64-gpl-8.1.tar.xz",
}

FFMPEG_URLS: dict[str, str] = {
    **{pid: BTBN_DOWNLOAD_BASE + asset for pid, asset in BTBN_ASSETS.items()},
    "macos-arm64": "https://evermeet.cx/ffmpeg/ffmpeg-8.1.zip",
    "macos-x64": "https://evermeet.cx/ffmpeg/ffmpeg-8.1.zip",
}


# ---- Pinned SHA-256 digests ----------------------------------------
#
# Each digest corresponds to the upstream URL above with the same key.
#
# BtbN digests: taken 2026-11-23 from the GitHub asset digests of
# ``autobuild-2026-10-31-12-59`` and cross-checked against that
# release's own ``checksums.sha256``:
#   https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-10-31-12-59/checksums.sha256
#
# evermeet.cx digest: computed 2026-04-30 by downloading the URL and
# running sha256sum; re-checked 2026-11-23.
#
# A mismatch at runtime is a hard failure (error="checksum_mismatch").
# An empty string / None / PLACEHOLDER_SENTINEL value is a hard failure
# at boot (error="placeholder_sha").

FFMPEG_SHA256: dict[str, str] = {
    # BTBN_DOWNLOAD_BASE + ffmpeg-n8.1.3-27-g8dca1586f4-win64-gpl-8.1.zip
    "windows-x64":
        "401dffcf14c301082e1fc03b0bc876b92ea676bfc521fc56e80a1537d137034f",
    # BTBN_DOWNLOAD_BASE + ffmpeg-n8.1.3-27-g8dca1586f4-linux64-gpl-8.1.tar.xz
    "linux-x64":
        "41e9c565594c390622cf968d82fa4135d9e9a11d06c1515644288cbc844e3380",
    # BTBN_DOWNLOAD_BASE + ffmpeg-n8.1.3-27-g8dca1586f4-linuxarm64-gpl-8.1.tar.xz
    "linux-arm64":
        "a08c0ee6e1c4e02139344b303002dcc7c37cf125d639f1fbd15dabee40fcb525",
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
