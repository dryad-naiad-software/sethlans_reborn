# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tail-of-log helper for the splash error card.

Extracted from :mod:`launcher.splash` to keep that module under the
300-line ceiling (see ``development/specs/splash_error_card_log_snippet.md``,
NFR-1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

LOG_SNIPPET_MAX_LINES = 20
LOG_HEADER = "--- Recent launcher log ---"
TRACE_HEADER = "--- Traceback ---"


def read_recent_log_snippet(log_path: Optional[Path]) -> str:
    """Return the last ~20 lines of the launcher log, or "" on miss/fail.

    Reads the whole file (logs ship with a rotating handler), splits,
    and takes the tail. Bounded by ``LOG_SNIPPET_MAX_LINES`` so a
    multi-MB log does not balloon the splash text. Decodes with
    ``errors="replace"`` so a stray non-utf8 byte does not crash.
    """
    if log_path is None:
        return ""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    if not lines:
        return ""
    return "\n".join(lines[-LOG_SNIPPET_MAX_LINES:])


__all__ = [
    "LOG_SNIPPET_MAX_LINES",
    "LOG_HEADER",
    "TRACE_HEADER",
    "read_recent_log_snippet",
]
