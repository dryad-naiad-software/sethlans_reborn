# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Release of a job's resources (Unreal foundation FR-WX-4, FR-WX-7).

One place for the release rules shared by the claim path, the
thread-start failure path and the job lifecycle:

* **Order.** The renderer reservation is released *before* the capacity
  slot. While the slots are still marked full the poll thread does not
  poll, so it can never see free slots while the Unreal token is still
  held (Conc R2-L1).
* **Retained.** When the renderer reservation is retained
  (``retain_until_restart``), neither it nor the slot is released; the
  machine takes no further work until the worker restarts (Conc R2-M1).
* **Claim-scoped.** Slots are released by job id, so a release must
  never outlive its claim. Two guarantees keep it so: ``job_selection``
  never claims an id that is still in ``_unfinished_job_ids`` (no two
  live threads per id), and the lifecycle's outermost safety net skips
  the releases once the inner ``finally`` did them
  (``job_data['_resources_released']``). ``_active_jobs`` is popped only
  when the entry is the one this claim recorded (``_active_entry``).

Locks: ``WorkerCapacity._lock`` and ``_active_jobs_lock`` are taken one
after the other, never nested, in the documented order.
"""
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

RESOURCES_RELEASED = '_resources_released'
ACTIVE_ENTRY = '_active_entry'


def release_reservations(job_id, renderer_reservation, capacity,
                         log_retained: bool = True) -> bool:
    """Release the renderer reservation, then the capacity slot.

    The slot is freed only when ``release()`` returns True (or there is no
    reservation); a retained reservation keeps both. Returns whether the
    slot was released. ``renderer_reservation`` and ``capacity`` may be None.
    """
    if renderer_reservation is not None and not renderer_reservation.release():
        if log_retained:
            logger.warning(
                "Job %s left processes behind; its renderer reservation and "
                "capacity slots stay held until the worker restarts.", job_id,
            )
        return False
    if capacity is not None:
        capacity.release_slot(job_id)
    return True


def forget_job(job_data: Dict[str, Any], *, unfinished: bool) -> None:
    """Drop this claim's ``_active_jobs`` entry (and its unfinished id).

    The entry is popped only if it is the object this claim recorded; a
    job dict without ``_active_entry`` (not claimed through ``job_claim``)
    pops unconditionally, as before.
    """
    from sethlans_worker_agent import job_processor
    job_id = job_data.get('id')
    with job_processor._active_jobs_lock:
        current = job_processor._active_jobs.get(job_id)
        if ACTIVE_ENTRY not in job_data or current is job_data[ACTIVE_ENTRY]:
            job_processor._active_jobs.pop(job_id, None)
        if unfinished:
            job_processor._unfinished_job_ids.discard(job_id)


def release_after_job(job_data: Dict[str, Any]) -> None:
    """Outermost safety net of ``process_claimed_job`` (FR-WX-4).

    Repeats the releases only if the inner ``finally`` never ran (an
    exception before the lifecycle's inner ``try``), then pops this
    claim's ``_active_jobs`` entry and removes the id from
    ``_unfinished_job_ids`` (FR-RT-17). Never raises.
    """
    from sethlans_worker_agent import job_processor
    job_id = job_data.get('id')
    try:
        if not job_data.get(RESOURCES_RELEASED):
            release_reservations(
                job_id, job_data.get('_renderer_reservation'),
                job_processor.get_worker_capacity(), log_retained=False,
            )
    except Exception:  # noqa: BLE001 - safety net must not raise
        logger.exception("Safety-net release failed for job %s", job_id)
    finally:
        forget_job(job_data, unfinished=True)


def release_after_dispatch_failure(job_data: Dict[str, Any],
                                   capacity: Optional[Any]) -> None:
    """Undo a claim whose job thread could not start (FR-WX-7).

    Releases the renderer reservation, then the slot, the ``_active_jobs``
    entry and the ``_unfinished_job_ids`` entry. A job whose renderer
    supports release (Unreal) is handed back to the manager with reason
    ``dispatch_failed``; Blender jobs keep today's manager-side state.
    """
    from sethlans_worker_agent import executors
    job_id = job_data.get('id')
    release_reservations(job_id, job_data.get('_renderer_reservation'), capacity)
    job_data[RESOURCES_RELEASED] = True
    forget_job(job_data, unfinished=True)
    if executors.supports_release(job_data.get('renderer')):
        _release_on_manager(job_id, 'dispatch_failed')


def request_shutdown_for_release(job_ids) -> None:
    """Drift handler path for releasable (Unreal) jobs (FR-WX-14).

    Sets the agent's shutdown event without PATCHing CANCELED, so each
    executor's shutdown path hands its job back to the manager (D4).
    Never raises.
    """
    logger.warning(
        "Drift handler: jobs %s will be released through the shutdown path.",
        sorted(job_ids),
    )
    try:
        from sethlans_worker_agent import agent
        agent._shutdown_event.set()
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - drift must proceed
        logger.error("Could not set the shutdown event for drift: %s", exc)


def _release_on_manager(job_id, reason: str) -> None:
    """POST ``jobs/{id}/release/`` through ``api_extras`` (FR-RT-11)."""
    try:
        from sethlans_worker_agent import api_extras
        api_extras.release_job(job_id, reason)
    except Exception:  # noqa: BLE001 - best effort; the reaper is the backstop
        logger.exception(
            "Could not release job %s to the manager (%s).", job_id, reason,
        )
