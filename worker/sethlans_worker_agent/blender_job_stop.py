# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-job stop of a Blender render whose card can no longer be used (FR-CL-5, C3).

At claim, on the agent-loop thread, each Blender job gets its own stop event
(``register_card_stop``), kept in its dict and in a registry by job id.
``request_card_stop(job_id)`` sets it (the card check, on the agent-loop
thread); it does nothing for an unknown id or a negative (retained) owner.
The worker's shutdown event is not touched. The executor joins the event
with the shutdown event (``job_stop``), so every point that stops on a
shutdown stops on it too, with reason ``worker_hardware_changed``; the job
is handed back as on a shutdown. The registry entry is removed when the
job's resources are released (``job_resources.release_after_job``).

Re-exported by ``blender_stop``. ``_stops_lock`` guards the registry only
and is never held with another lock; the events' own locks are leaves.
"""
import logging
import threading
from typing import Dict

logger = logging.getLogger(__name__)

CARD_STOP_KEY = '_card_stop_event'
WORKER_SHUTDOWN = 'worker_shutdown'
WORKER_HARDWARE_CHANGED = 'worker_hardware_changed'

_stops_lock = threading.Lock()
_card_stops: Dict[int, threading.Event] = {}


class JobStop:
    """The worker's shutdown event joined with one job's own stop event.

    Used wherever the shutdown event was: ``is_set()`` when either is set;
    ``reason`` is ``worker_hardware_changed`` once the job's own event is set.
    """

    def __init__(self, shutdown_event, own: threading.Event):
        self.shutdown_event = shutdown_event
        self.own = own

    def is_set(self) -> bool:
        return self.own.is_set() or (self.shutdown_event is not None and self.shutdown_event.is_set())

    @property
    def reason(self) -> str:
        return WORKER_HARDWARE_CHANGED if self.own.is_set() else WORKER_SHUTDOWN


def job_stop(shutdown_event, job_data):
    """``shutdown_event`` joined with the job's own stop event, or unchanged when the job has none."""
    own = job_data.get(CARD_STOP_KEY) if isinstance(job_data, dict) else None
    if own is None or isinstance(shutdown_event, JobStop):
        return shutdown_event
    return JobStop(shutdown_event, own)


def stop_reason(stop_event) -> str:
    """The hand-back reason of a stop seen on ``stop_event`` (a shutdown event or a ``JobStop``)."""
    return stop_event.reason if isinstance(stop_event, JobStop) else WORKER_SHUTDOWN


def register_card_stop(job_data) -> None:
    """Give a claimed Blender job its own stop event (agent-loop thread, at claim)."""
    event = threading.Event()
    job_data[CARD_STOP_KEY] = event
    with _stops_lock:
        _card_stops[job_data.get('id')] = event


def request_card_stop(job_id) -> bool:
    """Stop this job's render because its card can no longer be used; ``False`` for an unknown or retained id."""
    if not isinstance(job_id, int) or isinstance(job_id, bool) or job_id < 0:
        return False
    with _stops_lock:
        event = _card_stops.get(job_id)
    if event is None:
        return False
    if not event.is_set():
        logger.warning("Job %s: its graphics card can no longer be used; stopping the render and handing it "
                       "back (%s).", job_id, WORKER_HARDWARE_CHANGED)
        event.set()
    return True


def forget_card_stop(job_data) -> None:
    """Remove this claim's registry entry (never a later claim's of the same id)."""
    event = job_data.get(CARD_STOP_KEY) if isinstance(job_data, dict) else None
    if event is None:
        return
    with _stops_lock:
        if _card_stops.get(job_data.get('id')) is event:
            del _card_stops[job_data.get('id')]


def registered_ids_for_tests() -> list:
    """The job ids with a registered stop event, sorted. Test-only hook."""
    with _stops_lock:
        return sorted(_card_stops)


def reset_for_tests() -> None:
    """Forget every per-job stop event. Test-only hook."""
    with _stops_lock:
        _card_stops.clear()
