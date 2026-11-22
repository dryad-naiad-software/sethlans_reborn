# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Unreal claims that are still starting, for the start-up rule (render per card FR-PC-SU, D13, D16).

Keyed by ``claim_id`` (never by job id, D16):

* ``mark(claim_id)``: ``try_reserve``, on the agent-loop thread, just before it returns the
  reservation (every claim is marked, whatever the rule);
* ``clear(claim_id)``: the job thread once the render phase's first output file is there (a picture
  render; an audio or shots job stays starting until it ends), the reservation's ``on_release`` on
  every release path, and ``retain_until_restart``. Clearing a claim that is not marked does nothing.

``admits(rule)`` is reserver check 0b: under ``one_at_a_time`` no other claim may be starting;
under ``together`` nothing is checked. Only the agent-loop thread marks and checks, so no claim can
be marked between check 0b and the mark of the claim being reserved.

``_lock`` is a leaf lock (NF-1): nothing else is called while it is held.
"""
import threading
from typing import FrozenSet, Set

from sethlans_worker_agent import config_unreal

STILL_STARTING = "another Unreal render on this machine is still starting."


class StartupWatch:
    """One per worker process (``watch``), fresh ones in tests."""

    def __init__(self):
        self._lock = threading.Lock()
        self._starting: Set[str] = set()

    def mark(self, claim_id: str) -> None:
        with self._lock:
            self._starting.add(claim_id)

    def clear(self, claim_id: str) -> bool:
        """True when the claim was starting until now."""
        with self._lock:
            if claim_id not in self._starting:
                return False
            self._starting.discard(claim_id)
            return True

    def starting(self) -> FrozenSet[str]:
        with self._lock:
            return frozenset(self._starting)

    def admits(self, rule: str) -> bool:
        """Check 0b: True unless the rule is ``one_at_a_time`` and a claim is starting."""
        if rule != config_unreal.STARTUP_ONE_AT_A_TIME:
            return True
        with self._lock:
            return not self._starting


watch = StartupWatch()


def reset_for_tests() -> None:
    """Empty the worker's watch (test isolation only)."""
    with watch._lock:
        watch._starting.clear()
