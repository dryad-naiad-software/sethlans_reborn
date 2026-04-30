# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``ffmpeg -version`` execution and major-version parsing.

All subprocess invocations use ``shell=False`` and pass the binary
path as a single argv element.  No string formatting into a command
line.  No ``shell=True``.
"""

from __future__ import annotations

import logging
import re
import subprocess
from typing import Optional, Union
from pathlib import Path

logger = logging.getLogger(__name__)

# Tolerance for the verify subprocess.  ``ffmpeg -version`` is
# constant-time on a healthy install; 10 s is a generous ceiling for
# slow disks / warm-up.
VERIFY_TIMEOUT_SECONDS = 10

# Matches ``ffmpeg version 8.1`` and ``ffmpeg version N-99999-...``
# style version strings.  Anchored on the ``ffmpeg version`` prefix to
# reject the ``avlib`` lines further down the output.
_VERSION_RE = re.compile(r"^ffmpeg\s+version\s+n?(\d+)\.", re.IGNORECASE)


def verify_runs(binary_path: Union[str, Path]) -> bool:
    """Return ``True`` iff ``<binary_path> -version`` exits 0.

    Catches the standard set of subprocess failure modes
    (``FileNotFoundError``, ``TimeoutExpired``, ``OSError``).  Detailed
    diagnostics go to the manager log; the boolean return is the only
    thing the caller propagates into the closed-vocabulary ``error``.
    """
    path_str = str(binary_path)
    try:
        result = subprocess.run(
            [path_str, "-version"],
            capture_output=True,
            timeout=VERIFY_TIMEOUT_SECONDS,
            shell=False,
        )
    except FileNotFoundError:
        logger.error("verify_runs: %s does not exist", path_str)
        return False
    except subprocess.TimeoutExpired:
        logger.error("verify_runs: %s timed out", path_str)
        return False
    except OSError:
        logger.exception("verify_runs: OSError invoking %s", path_str)
        return False
    if result.returncode != 0:
        logger.error(
            "verify_runs: %s exited %d", path_str, result.returncode,
        )
        return False
    return True


def parse_major_version(binary_path: Union[str, Path]) -> Optional[int]:
    """Run ``<binary_path> -version`` and return the parsed major int.

    Returns ``None`` if the subprocess fails, hits a timeout, or the
    output cannot be parsed.  Used in tandem with ``verify_runs`` to
    enforce the ``>= 8`` version gate.
    """
    path_str = str(binary_path)
    try:
        result = subprocess.run(
            [path_str, "-version"],
            capture_output=True,
            timeout=VERIFY_TIMEOUT_SECONDS,
            shell=False,
            text=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    first_line = (result.stdout or "").splitlines()[0:1]
    if not first_line:
        return None
    match = _VERSION_RE.match(first_line[0])
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None
