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
  earlier periodic run). The periodic comparison still runs every heartbeat
  interval (30 s by default); the floor only limits the extra,
  request-driven detections (each initialises every GPU on the machine)
  when device checks keep failing for a reason a fresh detection cannot
  see.

A comparison whose detection failed (``drift.DETECTION_FAILED``, "try
again later") does not use up the request it answered: the request is put
back, and the floor limits how soon it runs again (#253).

Two known, bounded windows: a request that arrives between a periodic
turn's ``is_set()`` and ``clear()`` is answered by that same run, and if
that run's detection fails it waits for the next periodic run; and a
periodic turn that consumed a request and then failed puts it back, which
can cause one extra request-driven detection on the next iteration before
the floor applies.

The request is a ``threading.Event``: its own lock is a leaf, held only
inside set()/clear()/is_set() and never while another worker lock is
taken. ``_last_immediate_ts`` is read and written only by the agent-loop
thread, so it needs no lock.
"""
import threading
from typing import Optional

from sethlans_worker_agent.capacity.drift import DETECTION_FAILED

# Shortest time between two immediate (request-driven) comparisons.
IMMEDIATE_RECHECK_FLOOR_SECONDS = 300.0

_gpu_recheck_requested = threading.Event()
_last_immediate_ts: Optional[float] = None
# Whether the last turn consumed a pending request (agent-loop thread only).
_turn_consumed_request = False


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
    global _last_immediate_ts, _turn_consumed_request
    pending = _gpu_recheck_requested.is_set()
    if periodic_due:
        _gpu_recheck_requested.clear()
        _turn_consumed_request = pending
        return True
    if not pending:
        return False
    if (_last_immediate_ts is not None
            and now - _last_immediate_ts < IMMEDIATE_RECHECK_FLOOR_SECONDS):
        return False
    _gpu_recheck_requested.clear()
    _turn_consumed_request = True
    _last_immediate_ts = now
    return True


def run_gpu_list_check(capacity) -> None:
    """Run ``capacity.assert_gpu_list_unchanged()``; on an error, re-request.

    The turn already consumed the request, so a comparison that raises puts
    it back before re-raising instead of losing it, and one whose detection
    failed puts back a request it had consumed.
    """
    try:
        outcome = capacity.assert_gpu_list_unchanged()
    except BaseException:
        request_gpu_list_recheck()
        raise
    if outcome == DETECTION_FAILED and _turn_consumed_request:
        request_gpu_list_recheck()


def reset_gpu_list_recheck() -> None:
    """Clear the request and the floor. Test-only hook."""
    global _last_immediate_ts, _turn_consumed_request
    _gpu_recheck_requested.clear()
    _last_immediate_ts = None
    _turn_consumed_request = False
