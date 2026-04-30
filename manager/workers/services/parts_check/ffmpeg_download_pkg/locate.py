# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Filesystem locators for the bundled FFmpeg binary.

The bundled install root is::

    <data_dir>/bin/ffmpeg/<FFMPEG_VERSION>/

The actual binary may be nested several levels deep inside the
extracted archive (BtbN ships ``ffmpeg-N.NN-latest-...../bin/ffmpeg``);
``rglob`` walks for the platform-specific binary file name.
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Optional

from .constants import FFMPEG_VERSION, ffmpeg_binary_name


def get_ffmpeg_dir(data_dir: Path) -> Path:
    """Return the bundled FFmpeg install directory."""
    return data_dir / "bin" / "ffmpeg" / FFMPEG_VERSION


def get_ffmpeg_binary(install_dir: Path) -> Optional[Path]:
    """Locate the ffmpeg binary anywhere inside ``install_dir``.

    Returns ``None`` when ``install_dir`` does not exist or contains no
    matching file.  The first regular-file match wins.
    """
    if not install_dir.exists():
        return None
    pattern = "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg"
    for candidate in install_dir.rglob(pattern):
        if candidate.is_file():
            return candidate
    # Defensive: ffmpeg_binary_name() should always match the rglob
    # pattern but kept separate so a future binary rename only edits
    # constants.py.
    fallback = ffmpeg_binary_name()
    if fallback != pattern:
        for candidate in install_dir.rglob(fallback):
            if candidate.is_file():
                return candidate
    return None
