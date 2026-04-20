# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Process-wide mutation lock for worker setup-endpoint state.

Serializes concurrent *mutating* setup-endpoint requests under the
new Waitress/WSGI serving path (Phase 4+ of the worker migration).
The wizard is an inherently single-user flow -- two concurrent
writers (duplicate browser tab, retry storm, scripted client race)
are always a bug.  Rather than queueing the second request and
risking torn state in the module-level globals under
``handlers_*.py``, we fail fast: the first acquirer proceeds, any
concurrent acquirer receives ``False`` and the caller is expected
to emit ``409 Conflict``.

Analog of the manager-side ``manager/workers/services/setup_lock.py``
(not yet implemented at the time of this writing; the worker version
lands first and the manager port pattern-matches off this shape).

Design constraints:

* **Non-blocking acquisition.**  A second request returns ``False``
  *immediately*; we do not queue.  This matches the manager spec's
  "two concurrent POST /api/setup/verify -> exactly one 200, one
  409" acceptance.
* **Module-level lock.**  One lock serializes all mutating setup
  endpoints -- not per-endpoint.  A cross-endpoint race (POST
  ``select-manager`` + POST ``enroll``) is just as bad as a
  same-endpoint race, so a single lock is simpler and safer.
* **``try/finally`` release discipline.**  The context manager
  guarantees release even if the handler raises.
* **Non-reentrant.**  Uses :class:`threading.Lock`, not
  :class:`threading.RLock`.  If the same thread reacquires, it gets
  ``False`` -- which is correct: a handler that re-enters another
  mutating handler has a design smell worth surfacing as a 409.
* **Read endpoints MUST NOT take this lock.**  Per manager spec
  M-NEW-3, GET /status during an in-flight mutation should return
  200, not 409 -- otherwise the wizard's poll loop gets a spinner
  storm.

Usage::

    from sethlans_worker_agent.web_ui.setup.lock import (
        setup_mutation_lock,
    )
    from sethlans_worker_agent.web_ui.http_helpers_wsgi import (
        send_json_wsgi,
    )

    def handle_set_topology(environ, start_response):
        with setup_mutation_lock() as acquired:
            if not acquired:
                return send_json_wsgi(
                    start_response,
                    {'error': 'Setup mutation in progress; '
                              'retry after current operation '
                              'completes.'},
                    status=409,
                )
            # ... mutate module-level wizard state ...
            return send_json_wsgi(start_response, {'status': 'ok'})
"""

import threading
from contextlib import contextmanager
from typing import Iterator

# Module-level, process-wide.  Safe to import from multiple
# handlers -- threading.Lock is re-entrant across imports (same
# object), and Waitress runs all request threads inside a single
# process.
_setup_lock = threading.Lock()


@contextmanager
def setup_mutation_lock() -> Iterator[bool]:
    """Non-blocking mutation-lock context manager.

    Attempts to acquire the process-wide setup mutation lock without
    blocking.  Yields ``True`` when the caller holds the lock (and
    must proceed to perform the mutation), or ``False`` when another
    mutation is already in flight (and the caller must emit
    ``409 Conflict``).

    The lock is always released on context exit when it was
    acquired, including when the ``with`` block raises.  When
    acquisition failed the context manager releases nothing on
    exit -- owning and releasing another thread's lock would be a
    correctness bug.

    Callers should keep the critical section small: hold only for
    the duration of the state mutation itself (and the sentinel
    write, if applicable).  Do NOT hold across outbound HTTP calls
    to the manager -- a slow enrollment round-trip would block
    every subsequent mutating request behind it.

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

    **Diagnostic / observability use only.**  The result is
    inherently racy: another thread may acquire or release between
    the check and any decision you make off the return value.  Do
    not gate request handling on this -- always go through
    :func:`setup_mutation_lock` so acquisition is atomic with the
    subsequent proceed/409 decision.

    Safe to call from logging or metrics code, or from tests that
    want to assert lock state after a deterministic sequence of
    operations.
    """
    return _setup_lock.locked()
