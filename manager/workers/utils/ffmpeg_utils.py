# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
FFmpeg detection utilities.

Provides functions to detect whether ffmpeg is available on the system
PATH and to resolve its full path.
"""

import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)


def ffmpeg_path():
    """
    Return the resolved path string from ``shutil.which('ffmpeg')``,
    or ``None`` if ffmpeg is not on PATH.
    """
    return shutil.which('ffmpeg')


def ffmpeg_available():
    """
    Check whether ffmpeg is available by running ``ffmpeg -version``.

    Returns ``True`` if the command exits with code 0 within 5 seconds,
    ``False`` otherwise.
    """
    try:
        result = subprocess.run(
            ['ffmpeg', '-version'],
            capture_output=True,
            timeout=5,
            shell=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False
    except subprocess.TimeoutExpired:
        return False
    except OSError:
        return False
