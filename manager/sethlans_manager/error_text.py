# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""One short line for an exception whose full text may hold database details.

Used for the exit-11 reasons of a failed migrate, collectstatic or first
start: they reach stderr, the log and the launcher, so they carry the
exception class and only the first line of its message, cut before any
``DETAIL:`` (PostgreSQL puts row values and key data there).
"""

__all__ = ["short_error"]

DETAIL_MARKER = "DETAIL:"


def short_error(exc: BaseException) -> str:
    """``"<ExceptionClass>: <first line, before DETAIL:>"``, or just the class name."""
    lines = str(exc).strip().splitlines()
    first = lines[0] if lines else ""
    first = first.split(DETAIL_MARKER, 1)[0].strip()
    name = type(exc).__name__
    return f"{name}: {first}" if first else name
