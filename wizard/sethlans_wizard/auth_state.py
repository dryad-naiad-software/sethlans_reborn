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

import secrets
import threading
import time
from typing import Optional

# FR-W7 — 10 attempts per source IP per minute.
_RATE_LIMIT_MAX: int = 10
_RATE_LIMIT_WINDOW: float = 60.0

# FR-W12 — single handoff-state lock instance for the whole wizard process.
_handoff_state_lock: threading.Lock = threading.Lock()

# Mutable state guarded by ``_handoff_state_lock``.
_session_token: Optional[str] = None
_attempts: dict[str, list[float]] = {}


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
# Test helper (NOT part of the runtime API)
# ---------------------------------------------------------------------

def reset_state_for_tests() -> None:
    """Wipe the rate-limiter and session token.

    Tests share module-level state across the test session; this helper
    keeps tests independent without monkeypatching internals. Production
    code MUST NOT call this.
    """
    global _session_token
    with _handoff_state_lock:
        _session_token = None
        _attempts.clear()
