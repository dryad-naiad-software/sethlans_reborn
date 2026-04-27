# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""In-process auth + session state for the wizard (Spec 1 / A3).

Implements the storage half of FR-W7 (rate-limit + single-active-session)
and FR-W12 (single handoff-state lock invariant). The handler in
``handlers/auth.py`` orchestrates the calls; this module owns the
mutable state and the lock.

Lock invariant (FR-W12 / CONC-v23-LOW-2):
* The wizard process declares EXACTLY ONE handoff-state lock — a single
  ``threading.Lock`` instance — shared by ``_done_sent`` (FR-W9a),
  ``_browser_redirect_acknowledged`` (FR-W14), the probe-result cache
  tuple (FR-W14), the in-memory session store (this module), and the
  rate-limiter dict (this module).
* :func:`get_handoff_lock` returns that single lock so other modules
  (A4's done / runtime-ready handlers) can acquire the same instance.
* The in-flight probe lock (FR-W14) is a SEPARATE lock and lives in
  the probe handler module, NOT here.

Lock-coupling rule (CRITICAL — CONC-v23-MED-1):
* The handoff-state lock MUST NOT be held when the in-flight probe
  lock is acquired. Once the in-flight probe lock is held, the
  handoff-state lock may be acquired and released in short critical
  sections. Never reverse: never hold the handoff-state lock and
  then try to acquire the in-flight probe lock.

Rate-limit semantics (FR-W7):
* ``_RATE_LIMIT_MAX = 10`` failed attempts per source IP per
  ``_RATE_LIMIT_WINDOW = 60`` seconds.
* Once an IP exceeds the threshold, :func:`is_rate_limited` returns
  True until the window slides forward enough to drop attempts below
  the threshold (i.e. ``time.monotonic()`` is used so wall-clock NTP
  steps cannot reset the rate limit early).
* "Attempts" are *failed* attempts only — a successful auth does not
  count toward the limit. The handler is responsible for calling
  :func:`record_attempt` only after a failed compare.

Session-token semantics (FR-W7 / FR-W-FE3b):
* Single active session: :func:`set_session_token` overwrites any
  prior token, invalidating it.
* :func:`validate_session_token` uses ``secrets.compare_digest`` for a
  constant-time check.
* :func:`clear_session_token` is invoked from A4's done handler when
  the wizard begins its polite-shutdown sequence (FR-W17).
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from typing import Optional

from wizard.sethlans_wizard._attempts_reaper import AttemptsReaper

logger = logging.getLogger(__name__)

# FR-W7 — 10 attempts per source IP per minute.
_RATE_LIMIT_MAX: int = 10
_RATE_LIMIT_WINDOW: float = 60.0

# FR-W12 — single handoff-state lock instance for the whole wizard process.
_handoff_state_lock: threading.Lock = threading.Lock()

# Mutable state guarded by ``_handoff_state_lock``.
_session_token: Optional[str] = None
_attempts: dict[str, list[float]] = {}
_done_sent: bool = False
_browser_redirect_acknowledged: bool = False


def get_handoff_lock() -> threading.Lock:
    """Return the singleton handoff-state lock (FR-W12).

    A4's done / runtime-ready handlers MUST acquire this same lock when
    they read or mutate ``_done_sent`` /
    ``_browser_redirect_acknowledged`` / the probe-result cache, so that
    auth state, done state, and probe state share one lock instance.
    """
    return _handoff_state_lock


# ---------------------------------------------------------------------
# Rate limiter (FR-W7)
# ---------------------------------------------------------------------

def _prune_attempts_locked(ip: str, now: float) -> list[float]:
    """Drop attempts older than the rate-limit window.

    Caller MUST hold ``_handoff_state_lock``. Returns the pruned list
    (also stored back into ``_attempts``).
    """
    bucket = _attempts.get(ip, [])
    cutoff = now - _RATE_LIMIT_WINDOW
    fresh = [ts for ts in bucket if ts >= cutoff]
    if fresh:
        _attempts[ip] = fresh
    else:
        _attempts.pop(ip, None)
    return fresh


def is_rate_limited(ip: str) -> bool:
    """Return True if *ip* has hit the per-minute failure ceiling.

    Uses ``time.monotonic()`` so wall-clock / NTP steps cannot reset
    the limit early (mirrors the FR-W14 cache-TTL choice).
    """
    now = time.monotonic()
    with _handoff_state_lock:
        fresh = _prune_attempts_locked(ip, now)
    return len(fresh) >= _RATE_LIMIT_MAX


def record_attempt(ip: str) -> None:
    """Record a failed auth attempt from *ip* (FR-W7)."""
    now = time.monotonic()
    with _handoff_state_lock:
        _prune_attempts_locked(ip, now)
        _attempts.setdefault(ip, []).append(now)
    # Issue #147 — start the reaper on first attempt so a scanner that
    # hits the auth endpoint once per IP and never returns cannot grow
    # ``_attempts`` without bound. Idempotent.
    _reaper.ensure_started()


def _reap_stale_buckets(now: float) -> int:
    """Drop ``_attempts`` buckets with no fresh timestamps.

    Walks every entry under ``_handoff_state_lock`` and removes IPs
    whose newest attempt is older than ``_RATE_LIMIT_WINDOW`` ago —
    these can never contribute to a rate-limit decision and only waste
    memory. Returns the number of buckets removed (test-visible).
    """
    cutoff = now - _RATE_LIMIT_WINDOW
    removed = 0
    with _handoff_state_lock:
        # Snapshot the keys because we mutate ``_attempts`` mid-walk.
        for ip in list(_attempts.keys()):
            bucket = _attempts.get(ip)
            if not bucket or max(bucket) < cutoff:
                _attempts.pop(ip, None)
                removed += 1
    return removed


# Issue #147 — single AttemptsReaper instance, lazily started by
# :func:`record_attempt`. Lives at module scope so tests can grab it
# via ``auth_state._reaper`` to drive synchronous tick-based
# assertions without waiting on the real interval.
_reaper: AttemptsReaper = AttemptsReaper(_reap_stale_buckets)


# ---------------------------------------------------------------------
# Session token (FR-W7 / FR-W-FE3b)
# ---------------------------------------------------------------------

def set_session_token(token: str) -> None:
    """Install *token* as the single active session token (FR-W7).

    Overwrites any prior token, invalidating it.
    """
    global _session_token
    with _handoff_state_lock:
        _session_token = token


def issue_session_token() -> str:
    """Generate, install, and return a fresh session token.

    Convenience wrapper around ``secrets.token_urlsafe(32)`` +
    :func:`set_session_token`. Returns the new token.
    """
    token = secrets.token_urlsafe(32)
    set_session_token(token)
    return token


def validate_session_token(provided: Optional[str]) -> bool:
    """Constant-time compare *provided* against the current session.

    Returns False if no session has been issued, *provided* is None,
    or the compare fails. Uses ``secrets.compare_digest``.
    """
    if not provided or not isinstance(provided, str):
        return False
    with _handoff_state_lock:
        current = _session_token
    if current is None:
        return False
    # secrets.compare_digest requires str-vs-str or bytes-vs-bytes.
    return secrets.compare_digest(provided, current)


def clear_session_token() -> None:
    """Drop the current session token (called from A4's done handler).

    FR-W7 mandates the session token is cleared from memory before the
    wizard's polite-shutdown delay (FR-W17). After this call,
    :func:`validate_session_token` returns False for every input.
    """
    global _session_token
    with _handoff_state_lock:
        _session_token = None


# ---------------------------------------------------------------------
# Done-sent flag (FR-W9a)
# ---------------------------------------------------------------------

def check_and_set_done_sent() -> bool:
    """Atomic check-and-set for the FR-W9a ``_done_sent`` flag.

    Returns True if this caller is the first to set the flag (and is
    therefore responsible for writing the ``.wizard_done`` marker).
    Returns False if the flag was already set (caller MUST treat the
    request as a no-op idempotent ``already_done`` response).

    Held under the singleton handoff-state lock per FR-W9a / FR-W12.
    The lock is held ONLY around the flag check-and-set — NOT around
    any I/O — per CONC-v23-MED-1.
    """
    global _done_sent
    with _handoff_state_lock:
        if _done_sent:
            return False
        _done_sent = True
        return True


def is_done_sent() -> bool:
    """Read the ``_done_sent`` flag (FR-W9a)."""
    with _handoff_state_lock:
        return _done_sent


# ---------------------------------------------------------------------
# Browser-redirect-acknowledged flag (FR-W14 / FR-W17)
# ---------------------------------------------------------------------

def mark_browser_redirect_acknowledged() -> bool:
    """Set the ``_browser_redirect_acknowledged`` flag.

    Returns True the FIRST time the flag transitions to True (FR-W14
    "first time it returns ready"), False on subsequent calls. The
    runtime-ready handler uses the True return value to start the
    FR-W17 condition (a) 3-second grace timer.

    Held under the singleton handoff-state lock per FR-W14 lock-scope
    rules.
    """
    global _browser_redirect_acknowledged
    with _handoff_state_lock:
        if _browser_redirect_acknowledged:
            return False
        _browser_redirect_acknowledged = True
        return True


def was_browser_redirect_acknowledged() -> bool:
    """Read the ``_browser_redirect_acknowledged`` flag (FR-W17)."""
    with _handoff_state_lock:
        return _browser_redirect_acknowledged


# ---------------------------------------------------------------------
# Test helper (NOT part of the runtime API)
# ---------------------------------------------------------------------

def reset_state_for_tests() -> None:
    """Wipe the rate-limiter and session token.

    Tests share module-level state across the test session; this helper
    keeps tests independent without monkeypatching internals. Production
    code MUST NOT call this.

    Issue #147 — also signals the reaper daemon to exit so tests don't
    accumulate zombie threads. The next :func:`record_attempt` call
    will re-spawn it via :meth:`AttemptsReaper.ensure_started`.
    """
    global _session_token, _done_sent, _browser_redirect_acknowledged
    with _handoff_state_lock:
        _session_token = None
        _attempts.clear()
        _done_sent = False
        _browser_redirect_acknowledged = False
    # Stop the reaper outside the handoff lock — the reaper's iteration
    # body acquires the handoff lock and we don't want to risk a
    # join-while-locked stall.
    _reaper.stop()
