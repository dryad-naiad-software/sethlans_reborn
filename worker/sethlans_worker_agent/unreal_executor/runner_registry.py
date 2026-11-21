# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Every UE process runner of one ``execute()`` (Unreal foundation FR-UE-2, FR-UE-11).

``RunnerRegistry.confirm_all()`` is the job thread's outermost ``finally``: it repeats the exit
confirmation of every runner (idempotent) and returns the survivors; a runner whose confirmation
raises counts as an unknown survivor (fail closed). Re-exported by ``process_runner``.
"""
import logging
import threading
from typing import List, Tuple

logger = logging.getLogger(__name__)

UNKNOWN_SURVIVOR = ((0, 'unknown (exit confirmation failed)'),)


class RunnerRegistry:
    """Every runner of one ``execute()``; ``confirm_all`` is its outermost ``finally``."""

    def __init__(self):
        self._lock = threading.Lock()  # leaf lock
        self._runners: List = []

    def add(self, runner) -> None:
        with self._lock:
            self._runners.append(runner)

    def confirm_all(self) -> Tuple[Tuple[int, str], ...]:
        with self._lock:
            runners = list(self._runners)
        survivors = []
        for runner in runners:
            try:
                survivors.extend(runner.confirm_exit())
            except Exception:  # noqa: BLE001 - keep confirming the others; fail closed
                logger.exception("Exit confirmation failed.")
                survivors.extend(UNKNOWN_SURVIVOR)
        return tuple(survivors)
