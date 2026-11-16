# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
When the GPU list comparison runs (#239): periodically, and early after
a failed GPU device check.

A job thread whose render stopped on the GPU device check (#238) calls
``request_gpu_list_recheck()``: the card list this worker uses may be
stale. The agent-loop thread, the single consumer, asks
``take_gpu_list_check_turn()`` on every iteration whether to compare now:

- when the periodic comparison is due, it runs, and it answers a pending
  request too (the request is cleared);
- otherwise a pending request runs an immediate comparison, but after the
  first one only when at least ``IMMEDIATE_RECHECK_FLOOR_SECONDS`` have
  passed since the last immediate comparison. Inside the floor the request
  stays pending, so it is answered when the floor expires (or by an
  earlier periodic run). This bounds the detections, each of which
  initialises every GPU on the machine, when device checks keep failing
  for a reason a fresh detection cannot see.

The request is a ``threading.Event``: its own lock is a leaf, held only
inside set()/clear()/is_set() and never while another worker lock is
taken. ``_last_immediate_ts`` is read and written only by the agent-loop
thread, so it needs no lock.
"""
import threading
from typing import Optional

# Shortest time between two immediate (request-driven) comparisons.
IMMEDIATE_RECHECK_FLOOR_SECONDS = 300.0

_gpu_recheck_requested = threading.Event()
_last_immediate_ts: Optional[float] = None


def request_gpu_list_recheck() -> None:
    """Ask the agent loop to compare the GPU list early (any thread)."""
    _gpu_recheck_requested.set()


def gpu_list_recheck_pending() -> bool:
    """Return True while a request is pending. Does not clear it."""
    return _gpu_recheck_requested.is_set()


def take_gpu_list_check_turn(now: float, periodic_due: bool) -> bool:
    """Return True if the comparison should run now; consume what it answers.

    Single consumer: the agent-loop thread. ``now`` is a monotonic time and
    ``periodic_due`` says whether the heartbeat-interval comparison is due.
    The request is cleared BEFORE the comparison runs, so a request made
    while it runs stays pending for the next turn.
    """
    global _last_immediate_ts
    if periodic_due:
        _gpu_recheck_requested.clear()
        return True
    if not _gpu_recheck_requested.is_set():
        return False
    if (_last_immediate_ts is not None
            and now - _last_immediate_ts < IMMEDIATE_RECHECK_FLOOR_SECONDS):
        return False
    _gpu_recheck_requested.clear()
    _last_immediate_ts = now
    return True


def run_gpu_list_check(capacity) -> None:
    """Run ``capacity.assert_gpu_list_unchanged()``; on an error, re-request.

    The turn already consumed the request, so a comparison that raises puts
    it back before re-raising instead of losing it.
    """
    try:
        capacity.assert_gpu_list_unchanged()
    except BaseException:
        request_gpu_list_recheck()
        raise


def reset_gpu_list_recheck() -> None:
    """Clear the request and the floor. Test-only hook."""
    global _last_immediate_ts
    _gpu_recheck_requested.clear()
    _last_immediate_ts = None
