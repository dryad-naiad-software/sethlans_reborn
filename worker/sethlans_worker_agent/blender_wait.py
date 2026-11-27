# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
When version sync tries a Blender install: back-off after failures and the wait for the manager.

Version sync runs on the agent's main loop, once per heartbeat. ``attempt``
is its one install step: it skips a version inside its back-off or its
manager wait, else calls ``blender_install.install`` without blocking and
records the outcome. It never sleeps.

**Back-off (BS-WK-9, unchanged from version sync).** A failed install (the
manager step and the online step both failed: one failure) waits
``min(60 * 2**attempts, 1800)`` s before the next try. A busy or stopped
install is not a failure (BS-WK-3).

**Manager wait (BS-WK-10, D10).** While the manager answers ``queued`` or
``fetching``, the install returns ``WAITING`` with no online request. The
wait table keeps, per version, the time of the first such answer and the
next ask (+60 s); until then the version is skipped. Once 30 min have
passed since the first answer, the next attempt no longer waits: a
``queued``/``fetching`` answer then goes online at once. A ``ready``
answer, a successful install, or the version leaving the download actions
(``keep_waits``) clears the entry. A wait is not a failure: no back-off.

Times come from ``_clock`` (``time.monotonic``; tests replace it). Each
table has its own leaf lock, never held while installing or logging.
"""
import logging
import threading
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from sethlans_worker_agent import blender_install

logger = logging.getLogger(__name__)

_clock = time.monotonic  # injectable for tests

BACKOFF_BASE, BACKOFF_MAX = 60, 1800  # seconds
ASK_AGAIN_SECONDS = 60  # D10: ask a fetching manager again after 60 s
MAX_WAIT_SECONDS = 30 * 60  # D10: then go online

_failed_lock = threading.Lock()  # leaf; guards _failed_versions
_failed_versions: Dict[str, Dict] = {}  # version -> {'attempts': n, 'next_retry': clock time}

_wait_lock = threading.Lock()  # leaf; guards _waits


@dataclass
class _Wait:
    first_answer: float  # clock time of the first queued/fetching answer
    next_ask: float


_waits: Dict[str, _Wait] = {}

_NOT_TRIED = (blender_install.InstallOutcome.BUSY, blender_install.InstallOutcome.STOPPED)


def is_in_backoff(version: str) -> bool:
    with _failed_lock:
        info = _failed_versions.get(version)
        return bool(info and _clock() < info['next_retry'])


def record_failure(version: str) -> None:
    with _failed_lock:
        info = _failed_versions.get(version, {'attempts': 0})
        info['attempts'] += 1
        delay = min(BACKOFF_BASE * 2 ** info['attempts'], BACKOFF_MAX)
        info['next_retry'] = _clock() + delay
        _failed_versions[version] = info
        attempts = info['attempts']
    logger.warning(f"Blender {version} failed (attempt {attempts}). Retry in {delay}s.")


def clear_failure(version: str) -> None:
    with _failed_lock:
        _failed_versions.pop(version, None)


def is_waiting(version: str) -> bool:
    """True while ``version`` waits for the manager and its next ask has not come."""
    with _wait_lock:
        entry = _waits.get(version)
        return bool(entry and _clock() < entry.next_ask)


def waiting_versions() -> List[str]:
    """The versions that wait for the manager now (``is_waiting``), sorted; for the registration log (#329)."""
    now = _clock()
    with _wait_lock:
        return sorted(v for v, entry in _waits.items() if now < entry.next_ask)


def wait_expired(version: str) -> bool:
    """True when the manager has been fetching ``version`` for 30 min or more."""
    with _wait_lock:
        entry = _waits.get(version)
        return bool(entry and _clock() - entry.first_answer >= MAX_WAIT_SECONDS)


def record_wait(version: str) -> None:
    """The manager answered ``queued``/``fetching``: ask again in 60 s."""
    now = _clock()
    with _wait_lock:
        entry = _waits.get(version)
        if entry is None:
            _waits[version] = _Wait(first_answer=now, next_ask=now + ASK_AGAIN_SECONDS)
        else:
            entry.next_ask = now + ASK_AGAIN_SECONDS


def clear_wait(version: str) -> None:
    with _wait_lock:
        _waits.pop(version, None)


def keep_waits(versions: Iterable[str]) -> None:
    """Drop the wait of every version not in ``versions`` (the current download actions)."""
    keep = set(versions)
    with _wait_lock:
        for version in [v for v in _waits if v not in keep]:
            del _waits[version]


def reset() -> None:
    """Forget every failure and wait (tests)."""
    with _failed_lock:
        _failed_versions.clear()
    with _wait_lock:
        _waits.clear()


def skip_reason(version: str) -> Optional[str]:
    """Why version sync skips ``version`` this round, or None to try it."""
    if is_in_backoff(version):
        return "in backoff period"
    if is_waiting(version):
        return "waiting for the manager to fetch it"
    return None


def _install(version: str):
    """Non-blocking install that ends on agent shutdown (BS-WK-3); waits for a fetching manager
    until 30 min have passed (BS-WK-10)."""
    from sethlans_worker_agent.agent_shutdown import _shutdown_event  # lazy: agent_shutdown loads the web UI
    return blender_install.install(version, blocking=False, stop=_shutdown_event,
                                   wait_for_manager=not wait_expired(version))


def attempt(version: str) -> Optional[bool]:
    """One version-sync install of ``version``: True installed, False failed, None not tried this round.

    None covers a skip (back-off, manager wait), a busy or stopped install
    and a new manager wait; none of them is a failure.
    """
    if skip_reason(version) is not None:
        return None
    result = _install(version)
    if result.outcome is blender_install.InstallOutcome.WAITING:
        record_wait(version)
        return None
    if result.outcome in _NOT_TRIED:
        return None  # another install holds the version's lock, or shutdown: no back-off (BS-WK-3)
    if result.ok or result.manager_state == "ready":
        clear_wait(version)
    if result.ok:
        clear_failure(version)
        return True
    record_failure(version)
    return False
