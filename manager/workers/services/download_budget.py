# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Wall-clock budget guard shared by the setup-wizard streaming downloads.

Background
----------
``requests.get(..., stream=True, timeout=T)`` treats ``T`` as a
*per-socket-read* timeout, not an overall-transfer timeout. A server
that drips a byte every ``T - 1`` seconds can therefore keep a download
alive forever while holding a worker thread and an open partial file
(issue #114; same class of bug as #113 for the index fetch).

This module owns:

* :data:`DOWNLOAD_BUDGET_SECONDS` — the wall-clock ceiling applied on
  top of the per-read ``timeout=`` tuple. Patchable in tests.
* A tiny ``task_id`` set that records which downloads had their cancel
  event tripped by the budget timer (as opposed to a user clicking
  Cancel). Callers consult :func:`consume_budget_timeout` after a
  streamed download returns ``False`` to surface a distinct error state
  instead of collapsing both cases to "Cancelled".

Both :mod:`workers.services.blender_download` and
:mod:`workers.services.ffmpeg_download` use this — keeping the
machinery in one place avoids drift between the two near-duplicate
download helpers.
"""

from __future__ import annotations

import threading


# Wall-clock ceiling for the entire streamed transfer. A threading.Timer
# sets the shared cancel event when this elapses; the iter_content loop's
# existing cancel guard then exits. Patch to a tiny value (e.g. 0.5) in
# tests to exercise the timeout branch without waiting half an hour.
DOWNLOAD_BUDGET_SECONDS: float = 30 * 60


# Task IDs whose cancel event was tripped by the budget timer (not the
# user). The set is tiny (at most one entry per concurrent download) and
# callers pop their own entry via :func:`consume_budget_timeout` before
# returning, so it never grows unbounded.
_budget_timeouts: set[str] = set()
_budget_timeouts_lock = threading.Lock()


def mark_budget_timeout(task_id: str, cancel: threading.Event) -> None:
    """Timer callback: record that the budget fired, then trip cancel.

    Recording first and tripping after means a caller that observes
    ``cancel.is_set()`` is guaranteed to also observe the sentinel via
    :func:`consume_budget_timeout` — no ordering race.
    """
    with _budget_timeouts_lock:
        _budget_timeouts.add(task_id)
    cancel.set()


def consume_budget_timeout(task_id: str) -> bool:
    """Return True iff the budget fired for *task_id*; clears the entry.

    The "clear on read" contract means a second call for the same
    ``task_id`` returns False — callers must check exactly once per
    download.
    """
    with _budget_timeouts_lock:
        if task_id in _budget_timeouts:
            _budget_timeouts.remove(task_id)
            return True
        return False


def start_budget_timer(
    task_id: str, cancel: threading.Event,
) -> threading.Timer:
    """Start a daemon Timer that trips *cancel* after the budget.

    Callers MUST cancel the returned Timer in a ``finally`` block so it
    can't fire after the download returns and taint a later task.
    """
    t = threading.Timer(
        DOWNLOAD_BUDGET_SECONDS,
        mark_budget_timeout, args=(task_id, cancel),
    )
    t.daemon = True
    t.start()
    return t


def cancel_or_timeout_error(task_id: str) -> str:
    """Pick the right error string after ``_stream_download`` returns False.

    Centralised here so both blender and ffmpeg download modules surface
    identical language, and the user-cancel / timeout distinction can be
    verified in tests against a single source of truth.
    """
    if consume_budget_timeout(task_id):
        return (
            f"Download timed out (exceeded "
            f"{DOWNLOAD_BUDGET_SECONDS:.0f}s budget)"
        )
    return "Cancelled"
