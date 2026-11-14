# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Pick the job to claim from a poll response (Unreal foundation FR-WX-6).

``select_claimable_job`` walks the poll response in order (oldest first,
FR-RT-15) and returns the **first** job whose executor's ``try_reserve``
returns a reservation. There is no drain and no renderer preference (D1).
A job's renderer is ``job.get('renderer') or 'BLENDER'``: a missing key
or a JSON ``null`` means Blender (Dom N-6). An unknown renderer, an
executor that fails to load, or a ``try_reserve`` that raises is skipped
and logged, never raised into the poll loop (FR-WX-5).

Same job id twice: a job whose id is still in ``_unfinished_job_ids`` (a
previous run of it on this worker has not finished its report, upload or
release, e.g. after a cancel+requeue or a reaper requeue) is skipped, so
two live threads never share a job id; releases are by id. The poll
thread is the only adder to that set, so reading it once per call and
then claiming is race-free (a removal in between only causes a skip).

Pending probe (Conc R2-L2a): if the first job is declined only because
its share probe is still pending (``ProbePending``), the call returns
None for this cycle, so the next poll can use the probe's result. This
happens at most once per job id; on a later cycle the walk continues.

Skips are logged at INFO, at most once per job id per 10 minutes. The
record dict (shared with the pending-probe flag) is pruned of entries
older than 10 minutes on every call and capped at 1,000 entries. Pruning
also resets the pending-probe flag: a job still pending after the
600 s TTL can cause one more single-cycle wait.

Called from the poll thread only; the module state takes no lock.
"""
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sethlans_worker_agent import executors
from sethlans_worker_agent.executors import (
    ProbePending,
    RendererReservation,
    UnsupportedRendererError,
)

logger = logging.getLogger(__name__)

SKIP_RECORD_TTL_SECONDS = 600.0
SKIP_RECORD_MAX_ENTRIES = 1000

# Separate name so tests can drive time without patching time.monotonic
# for the whole process.
_clock = time.monotonic


@dataclass(frozen=True)
class Selection:
    """The chosen job plus what the claim path needs to reserve for it."""
    job: Dict[str, Any]
    renderer_reservation: RendererReservation
    capacity_mode: str


@dataclass
class _SkipRecord:
    stamp: float
    logged: bool = False
    waited: bool = False


_skip_records: Dict[Any, _SkipRecord] = {}


def _prune(now: float) -> None:
    expired = [
        job_id for job_id, rec in _skip_records.items()
        if now - rec.stamp > SKIP_RECORD_TTL_SECONDS
    ]
    for job_id in expired:
        del _skip_records[job_id]
    overflow = len(_skip_records) - SKIP_RECORD_MAX_ENTRIES
    if overflow > 0:
        oldest = sorted(_skip_records, key=lambda j: _skip_records[j].stamp)
        for job_id in oldest[:overflow]:
            del _skip_records[job_id]


def _record(job_id, now: float) -> _SkipRecord:
    rec = _skip_records.get(job_id)
    if rec is None:
        rec = _SkipRecord(stamp=now)
        _skip_records[job_id] = rec
        _prune(now)
    return rec


def _log_skip(job_id, renderer, reason: str, now: float) -> None:
    rec = _record(job_id, now)
    if rec.logged:
        return
    rec.logged = True
    logger.info("Skipping %s job %s: %s", renderer, job_id, reason)


def _first_wait_for_probe(job_id, now: float) -> bool:
    """True (and remember it) the first time this job causes a wait."""
    rec = _record(job_id, now)
    if rec.waited:
        return False
    rec.waited = True
    return True


def _resolve_executor(job, renderer, now):
    try:
        return executors.get_executor(renderer)
    except UnsupportedRendererError as exc:
        _log_skip(job.get('id'), renderer, str(exc), now)
    except Exception as exc:  # noqa: BLE001 - never raise into the poll loop
        _log_skip(job.get('id'), renderer, f"executor lookup failed: {exc}", now)
    return None


def _try_job(job, is_first: bool, now: float, unfinished):
    """Return ``(selection, stop)`` for one job of the poll response."""
    job_id = job.get('id')
    renderer = job.get('renderer') or executors.DEFAULT_RENDERER
    if job_id in unfinished:
        _log_skip(job_id, renderer, "previous run still finishing on this worker", now)
        return None, False
    executor = _resolve_executor(job, renderer, now)
    if executor is None:
        return None, False
    try:
        reservation = executor.try_reserve(job)
    except ProbePending:
        if is_first and _first_wait_for_probe(job_id, now):
            logger.info(
                "Share probe for job %s still pending; waiting one poll "
                "cycle before considering later jobs.", job_id,
            )
            return None, True
        _log_skip(job_id, renderer, "share probe still pending", now)
        return None, False
    except Exception as exc:  # noqa: BLE001 - never raise into the poll loop
        _log_skip(job_id, renderer, f"eligibility check failed: {exc}", now)
        return None, False
    if reservation is None:
        _log_skip(job_id, renderer, "not eligible on this worker now", now)
        return None, False
    return Selection(job, reservation, executor.capacity_mode), False


def select_claimable_job(available_jobs: List[Dict[str, Any]],
                         capacity) -> Optional[Selection]:
    """Return the first eligible job of the poll response, or None.

    ``capacity`` is the worker's ``WorkerCapacity``; executors read it
    through ``job_processor`` themselves, it is accepted here so the
    signature matches the spec and future executors can use it.
    """
    from sethlans_worker_agent import job_processor  # circular at import time
    now = _clock()
    _prune(now)
    unfinished = frozenset(job_processor.get_unfinished_job_ids())
    for position, job in enumerate(available_jobs):
        selection, stop = _try_job(job, position == 0, now, unfinished)
        if selection is not None or stop:
            return selection
    return None


def reset_for_tests() -> None:
    """Clear the skip records (test isolation only)."""
    _skip_records.clear()
