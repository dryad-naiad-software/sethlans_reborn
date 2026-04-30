# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Parts-check framework: boot-time verification of external dependencies
the manager needs at runtime.

Adding a new part is a matter of writing a check function and calling
``register_part(name, check_fn)`` at import time. The framework then
runs every registered check sequentially in a single background daemon
thread spawned from ``WorkersConfig.ready()``.

Public API:
    run_parts_check()       — kickoff (idempotent under multi-fire ready())
    get_status(name)        — thread-safe snapshot read
    register_part(name, fn) — add a check at import time

The FFmpeg part registers itself when this module is imported.
"""

from .registry import (
    get_status,
    register_part,
    run_parts_check,
)
from . import ffmpeg as _ffmpeg_part  # noqa: F401  (registers on import)

__all__ = [
    "get_status",
    "register_part",
    "run_parts_check",
]
