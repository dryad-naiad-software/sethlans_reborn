# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Parts-check registry: thread-safe storage of part status and the
idempotent kickoff that runs every registered check.

Concurrency model — two distinct primitives, neither nested:

1. ``_thread_started`` — a process-global ``bool`` guarded by
   ``_kickoff_lock``.  ``run_parts_check()`` takes the lock, checks the
   flag, sets it ``True``, releases the lock, and then spawns the daemon
   thread.  Subsequent ``run_parts_check()`` invocations (e.g. from
   multi-fire ``AppConfig.ready()``) take the lock, see the flag, and
   return early.  **The lock is never held across the actual check**;
   it scopes only the flag mutation.

2. ``_status_lock`` — a separate ``threading.Lock`` that scopes the
   registry-mutation reference swap when a check completes.  The check
   thread builds a frozen ``Status`` object outside the lock, then takes
   the lock briefly to swap the registry's stored reference, then
   releases.  Readers (``get_status``) take the lock briefly to copy the
   reference, release, and inspect.  Readers never block on the check
   thread.

Status objects are ``@dataclass(frozen=True)`` so a published status
cannot be mutated in place — every reader sees a complete, well-formed
object after the brief lock-protected reference copy.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Status:
    """Immutable status snapshot for a registered part.

    ``status`` is one of: ``"installing"``, ``"ready"``, ``"failed"``.
    ``error`` is ``None`` unless ``status == "failed"``, in which case
    it is a closed-vocabulary string (see spec FR §70-79).
    Other fields (``source``, ``version``, ``path``) are part-specific
    and may be empty strings during the ``"installing"`` phase.
    """
    status: str = "installing"
    source: str = ""
    version: str = ""
    path: str = ""
    error: Optional[str] = None


# ---- Registry storage ----
# _parts maps name -> check_fn.  Mutated only at module-import time
# (registration), so no lock is needed for reads after startup.
_parts: dict[str, Callable[[], Status]] = {}

# _statuses maps name -> Status.  Read by API view, write by the check
# thread.  Reference swaps are scoped by _status_lock.
_statuses: dict[str, Status] = {}
_status_lock = threading.Lock()

# ---- Kickoff idempotency ----
_kickoff_lock = threading.Lock()
_thread_started: bool = False


def register_part(name: str, check_fn: Callable[[], Status]) -> None:
    """Register a part with its check function.

    Called at module-import time; not thread-safe by design (modules are
    imported under Python's import lock).  Initial status is
    ``"installing"`` so reads before the check completes return a
    well-formed snapshot.
    """
    _parts[name] = check_fn
    _statuses[name] = Status(status="installing")


def get_status(name: str) -> Status:
    """Return the most recent status for ``name``.

    Thread-safe: takes ``_status_lock`` briefly to copy the reference,
    then releases.  The returned object is frozen — callers cannot
    mutate it.

    Returns a default ``Status(status="installing")`` for an unknown
    part name, mirroring the just-after-registration state.
    """
    with _status_lock:
        return _statuses.get(name, Status(status="installing"))


def _publish(name: str, status: Status) -> None:
    """Atomic reference swap of a part's published status.

    Brief lock acquisition; the lock is never held while the check_fn
    runs.
    """
    with _status_lock:
        _statuses[name] = status


def _run_all_checks() -> None:
    """Sequentially run every registered check, publishing each result.

    Runs inside the single daemon thread spawned by
    ``run_parts_check()``.  Exceptions are caught per-part so one
    misbehaving check cannot prevent later checks from running.
    """
    for name, check_fn in _parts.items():
        try:
            result = check_fn()
        except Exception:
            logger.exception(
                "parts_check: %r raised an unexpected exception", name,
            )
            result = Status(
                status="failed",
                error="extraction_failed",  # generic catch-all bucket
            )
        _publish(name, result)
        logger.info(
            "parts_check: %s -> status=%s source=%s version=%s",
            name, result.status, result.source, result.version,
        )


def run_parts_check() -> None:
    """Kickoff entry-point — idempotent under multi-fire AppConfig.ready().

    The first caller takes ``_kickoff_lock``, checks ``_thread_started``,
    sets it ``True``, releases the lock, and spawns the daemon thread.
    Subsequent callers take the lock, see the flag already set, release
    and return — they do NOT spawn a second thread.
    """
    global _thread_started

    with _kickoff_lock:
        if _thread_started:
            return
        _thread_started = True

    thread = threading.Thread(
        target=_run_all_checks,
        name="parts-check",
        daemon=True,
    )
    thread.start()


def _reset_for_tests() -> None:
    """Test-only: reset module state so a fresh test run has a clean slate.

    NOT part of the public API.  Used by ``tests/unit/manager/test_*``
    modules to simulate process boundaries within a single pytest run.
    """
    global _thread_started
    with _kickoff_lock:
        _thread_started = False
    with _status_lock:
        for name in list(_statuses.keys()):
            _statuses[name] = Status(status="installing")
