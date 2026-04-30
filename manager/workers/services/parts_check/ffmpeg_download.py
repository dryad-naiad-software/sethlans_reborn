# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
FFmpeg download, SHA-256 verification, and atomic extract pipeline for
the parts-check framework.

Layout:
    constants    — pinned version, URLs, SHA-256 digests
    download     — HTTPS streaming GET into <final_dir>.tmp
    extract      — traversal-safe extraction into <final_dir>.partial
    verify       — verify_runs subprocess + version parsing
    install      — orchestrator (download + verify + extract + promote)

This file is the public interface: re-exports the orchestrator and the
helper accessors callers (``ffmpeg.py``) actually need.

All HTTPS downloads use the system trust store (``verify=True``).
``verify=False`` is forbidden.

Version is pinned to **8.1**.  Updating means bumping ``FFMPEG_VERSION``
plus all three SHA-256 constants in ``constants.py`` together.
"""

from .ffmpeg_download_pkg.constants import (
    FFMPEG_VERSION,
    FFMPEG_URLS,
    FFMPEG_SHA256,
    PLACEHOLDER_SENTINEL,
    get_platform_id,
    is_placeholder,
    ffmpeg_binary_name,
)
from .ffmpeg_download_pkg.install import download_and_install_bundled
from .ffmpeg_download_pkg.verify import verify_runs, parse_major_version
from .ffmpeg_download_pkg.cleanup import cleanup_stale_partials, cleanup_paths
from .ffmpeg_download_pkg.locate import get_ffmpeg_binary, get_ffmpeg_dir

__all__ = [
    "FFMPEG_VERSION",
    "FFMPEG_URLS",
    "FFMPEG_SHA256",
    "PLACEHOLDER_SENTINEL",
    "get_platform_id",
    "is_placeholder",
    "ffmpeg_binary_name",
    "download_and_install_bundled",
    "verify_runs",
    "parse_major_version",
    "cleanup_stale_partials",
    "cleanup_paths",
    "get_ffmpeg_binary",
    "get_ffmpeg_dir",
]
