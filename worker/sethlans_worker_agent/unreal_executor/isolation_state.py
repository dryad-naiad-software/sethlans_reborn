# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Known-folder node condition (Unreal foundation FR-UE-27 "Known-folder
check", FR-UE-1 check 6b).

The process-wide state of the known-folder check: the farm profile it is
run for, and the latest ``KnownFolderResult``.

* ``register(P)``: called once at worker start, after recovery has created
  the farm profile (FR-PA-18 step 1b; chunk 7b wires the call), runs the
  first check. Until it is called, nothing here runs a probe (NF-6, and
  the chunk-4 test fakes never call it).
* ``refresh()``: re-runs the check when the last one is at least
  ``RESCAN_SECONDS`` old (``runtime.rescan`` calls it with the engine scan).
* ``check_now()``: before each job's first UE launch.
* ``expect()``: called at worker start when Unreal is enabled, before
  recovery; from then until the first check ``reason()`` reports
  ``NOT_RUN_REASON`` (review L8).
* ``reason()``: ``disabled_reason`` while the latest check failed, or the
  not-run reason once expected (the capabilities report then says
  ``enabled=false``); empty otherwise.
  ``passed_within(max_age_s)`` is FR-UE-1 check 6b.

**Contract for chunk 7b:** call ``expect()`` then, after recovery step 1b,
``register(P)``; gate check 6b with ``passed_within(check_6b_max_age_s())``.
``runtime.rescan`` forces a refresh on every engine scan, so a result is
replaced at least every ``RESCAN_SECONDS`` plus the delay until the next
caller of ``rescan`` (at most one heartbeat interval) plus one probe
(``PROBE_TIMEOUT_SECONDS``). The margin over ``RESCAN_SECONDS`` must
therefore be at least the heartbeat interval + ``PROBE_TIMEOUT_SECONDS``;
``check_6b_max_age_s`` computes it (never below ``PASSED_WITHIN_MARGIN_S``).

A failure logs a WARNING at most once per ``WARN_INTERVAL_SECONDS``; the
first passing check after a failure makes the node eligible again.

Locking: ``_lock`` is a leaf lock over the fields; the probe itself runs
outside it, and ``_probe_lock`` (non-blocking) keeps one probe in flight.
"""
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from sethlans_worker_agent import config_unreal
from sethlans_worker_agent.unreal_executor import user_state

logger = logging.getLogger(__name__)

WARN_INTERVAL_SECONDS = 600
PASSED_WITHIN_MARGIN_S = 60
NOT_RUN_REASON = "the Windows known-folder check has not run yet; farm Unreal launches cannot be isolated"

_lock = threading.Lock()        # leaf lock
_probe_lock = threading.Lock()  # one probe at a time; taken non-blocking except by check_now
_profile: Optional[Path] = None
_result: Optional[user_state.KnownFolderResult] = None
_last_warning: Optional[float] = None
_expected = False
_clock: Callable[[], float] = time.monotonic
_check: Callable = user_state.check_known_folders


def _record(result: user_state.KnownFolderResult) -> user_state.KnownFolderResult:
    global _result, _last_warning
    with _lock:
        previous = _result
        _result = result
        warn = not result.ok and (_last_warning is None or _clock() - _last_warning >= WARN_INTERVAL_SECONDS)
        if warn:
            _last_warning = _clock()
    if warn:
        logger.warning("Unreal rendering disabled: %s", result.reason)
    elif result.ok and previous is not None and not previous.ok:
        logger.info("Known-folder check passed again; Unreal isolation is available.")
    return result


def _run(profile: Path) -> user_state.KnownFolderResult:
    try:
        result = _check(profile, clock=_clock)
    except Exception as exc:  # noqa: BLE001 - fail closed, never crash the caller
        logger.exception("Known-folder check failed.")
        result = user_state.KnownFolderResult(
            ok=False, folder='Local AppData', path=f'an error ({type(exc).__name__})',
            reason=user_state.REDIRECTED_REASON.format(folder='Local AppData',
                                                       path=f'an error ({type(exc).__name__})'),
            checked_at=_clock())
    return _record(result)


def register(profile) -> user_state.KnownFolderResult:
    """Remember the farm profile and run the first check (worker start)."""
    global _profile
    with _lock:
        _profile = Path(profile)
    with _probe_lock:
        return _run(Path(profile))


def refresh(force: bool = False) -> Optional[user_state.KnownFolderResult]:
    """Re-run the check when due (``RESCAN_SECONDS``); None when not registered."""
    with _lock:
        profile, result = _profile, _result
    if profile is None:
        return None
    due = force or result is None or _clock() - result.checked_at >= config_unreal.RESCAN_SECONDS
    if not due or not _probe_lock.acquire(blocking=False):
        return result
    try:
        return _run(profile)
    finally:
        _probe_lock.release()


def check_now() -> Optional[user_state.KnownFolderResult]:
    """One probe before a job's first UE launch; None when not registered."""
    with _lock:
        profile = _profile
    if profile is None:
        return None
    with _probe_lock:
        return _run(profile)


def expect() -> None:
    """A farm profile is expected: until the first check, ``reason()`` is ``NOT_RUN_REASON``."""
    global _expected
    with _lock:
        _expected = True


def latest() -> Optional[user_state.KnownFolderResult]:
    with _lock:
        return _result


def reason() -> str:
    """The FR-UE-27 disabled reason while the latest check failed (or has not run once expected), else ''."""
    with _lock:
        result, expected = _result, _expected
    if result is None:
        return NOT_RUN_REASON if expected else ''
    return result.reason if not result.ok else ''


def check_6b_max_age_s() -> float:
    """``RESCAN_SECONDS`` + max(``PASSED_WITHIN_MARGIN_S``, heartbeat interval + probe timeout)."""
    from sethlans_worker_agent import config
    heartbeat = max(1, int(getattr(config, 'HEARTBEAT_INTERVAL_SECONDS', 30)))
    margin = max(PASSED_WITHIN_MARGIN_S, heartbeat + user_state.PROBE_TIMEOUT_SECONDS)
    return float(config_unreal.RESCAN_SECONDS + margin)


def passed_within(max_age_s: float) -> bool:
    """FR-UE-1 check 6b: the latest check passed and is at most ``max_age_s`` old."""
    with _lock:
        result = _result
    return result is not None and result.ok and _clock() - result.checked_at <= max_age_s


def reset_for_tests() -> None:
    global _profile, _result, _last_warning, _expected
    with _lock:
        _profile, _result, _last_warning, _expected = None, None, None, False
