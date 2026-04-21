# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Process-wide mutation lock for manager setup-endpoint state.

Serializes concurrent *mutating* setup-endpoint requests under the
Waitress/WSGI serving path (manager spec Phase 3). The wizard is an
inherently single-user flow: two concurrent writers (duplicate browser
tab, retry storm, scripted client race) are always a bug. Rather than
queueing the second request and risking torn state (torn
``manager.ini`` writes, racing sentinel writes, overlapping Caddy
restarts), we fail fast: the first acquirer proceeds, any concurrent
acquirer receives ``False`` and the caller is expected to emit
``409 Conflict``.

Analog of the worker-side
``worker/sethlans_worker_agent/web_ui/setup/lock.py``.

Design constraints (per manager spec M-NEW-3):

* **Non-blocking acquisition.** A second request returns ``False``
  *immediately*; we do not queue. This matches the spec's "two
  concurrent POST /api/setup/verify -> exactly one 200, one 409"
  acceptance.
* **Module-level lock.** One lock serializes all mutating setup
  endpoints — not per-endpoint. A cross-endpoint race (POST network +
  POST verify) is just as bad as a same-endpoint race, so a single
  lock is simpler and safer.
* **``try/finally`` release discipline.** The context manager
  guarantees release even if the handler raises.
* **Non-reentrant.** Uses :class:`threading.Lock`, not
  :class:`threading.RLock`. If the same thread reacquires, it gets
  ``False`` — which is correct: a handler that re-enters another
  mutating handler has a design smell worth surfacing as a 409.
* **GET endpoints MUST NOT take this lock.** Per spec M-NEW-3,
  ``GET /api/setup/status`` during an in-flight mutation should
  return 200, not 409 — otherwise the wizard's poll loop gets a
  spinner storm during a 5-second Caddy restart.

Usage::

    from workers.services.setup_lock import (
        setup_mutation_lock,
        setup_conflict_response,
    )

    @api_view(["POST"])
    def my_setup_endpoint(request):
        with setup_mutation_lock() as acquired:
            if not acquired:
                return setup_conflict_response()
            # ... mutate state, write sentinel, etc. ...
            return Response({"status": "ok"})
"""

import threading
from contextlib import contextmanager
from typing import Iterator

from rest_framework import status
from rest_framework.response import Response

# Module-level, process-wide. Waitress runs all request threads inside
# a single process, so a single lock instance is sufficient.
_setup_lock = threading.Lock()


@contextmanager
def setup_mutation_lock() -> Iterator[bool]:
    """Non-blocking mutation-lock context manager.

    Attempts to acquire the process-wide setup mutation lock without
    blocking. Yields ``True`` when the caller holds the lock (and
    must proceed to perform the mutation), or ``False`` when another
    mutation is already in flight (and the caller must emit
    ``409 Conflict``).

    The lock is always released on context exit when it was
    acquired, including when the ``with`` block raises. When
    acquisition failed the context manager releases nothing on exit
    — owning and releasing another thread's lock would be a
    correctness bug.

    Callers should keep the critical section small: hold only for
    the duration of the state mutation itself (and the sentinel
    write, if applicable). Do NOT hold across outbound HTTP calls
    (e.g. polling a Caddy listener) unless the specific endpoint
    requires it — a slow round-trip would block every subsequent
    mutating request behind it.

    Callers should NOT wrap GET handlers in this context manager;
    reads are lock-free by design (see module docstring).
    """
    acquired = _setup_lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            _setup_lock.release()


def is_setup_mutation_in_progress() -> bool:
    """Return whether a mutation currently holds the setup lock.

    **Diagnostic / observability use only.** The result is
    inherently racy: another thread may acquire or release between
    the check and any decision made off the return value. Do not
    gate request handling on this — always go through
    :func:`setup_mutation_lock` so acquisition is atomic with the
    subsequent proceed/409 decision.
    """
    return _setup_lock.locked()


def setup_conflict_response() -> Response:
    """Return the canonical 409 Conflict response for the lock path.

    All mutating setup endpoints should return this when the lock
    is held by another request. Centralised here so the error shape
    is identical across endpoints (wizard UX depends on a stable
    envelope).
    """
    return Response(
        {
            "error": {
                "code": "setup_in_progress",
                "message": (
                    "Setup mutation in progress; retry after the "
                    "current operation completes."
                ),
                "details": {},
            },
        },
        status=status.HTTP_409_CONFLICT,
    )
