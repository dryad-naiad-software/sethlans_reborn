# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Parts-check framework: boot-time verification of external dependencies
the manager needs at runtime.

Adding a new part is a matter of writing a check function and calling
``register_part(name, check_fn)`` at import time. The framework then
runs every registered check sequentially in a single background daemon
thread, started by the serving process only (issue #294): the Waitress
launcher, and ``runserver``'s serving process in development.  See
``workers.parts_check_startup``.

Public API:
    run_parts_check()       — kickoff (idempotent, once per process)
    wait_for_checks(secs)   — wait for every check to publish
    checks_in_progress()    — True while the check thread runs
    get_status(name)        — thread-safe snapshot read
    register_part(name, fn) — add a check at import time

The FFmpeg part registers itself when this module is imported.
"""

from .registry import (
    checks_in_progress,
    get_status,
    register_part,
    run_parts_check,
    wait_for_checks,
)
from . import ffmpeg as _ffmpeg_part  # noqa: F401  (registers on import)

__all__ = [
    "checks_in_progress",
    "get_status",
    "register_part",
    "run_parts_check",
    "wait_for_checks",
]
