# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Claim the job ``job_selection`` chose (Unreal foundation FR-WX-7).

Split out of ``job_processor`` to keep that module under 300 lines.
``api_handler.claim_job`` is reached through module-attribute lookup, so
tests patching ``sethlans_worker_agent.api_handler.claim_job`` still apply.

One ``try/finally`` with a ``claimed`` flag covers everything after
selection. On every non-success exit (no compatible slot, shutdown in
progress, ``claim_job`` False, any exception) it releases the renderer
reservation, then the capacity slot if one was reserved.
"""
import datetime
import logging
from typing import Any, Dict, Optional

from sethlans_worker_agent import api_handler
from sethlans_worker_agent.job_resources import ACTIVE_ENTRY, forget_job, release_reservations

logger = logging.getLogger(__name__)


def _shutdown_requested() -> bool:
    """True once the agent's shutdown event is set (Conc R2-L7)."""
    try:
        from sethlans_worker_agent import agent
        return agent._shutdown_event.is_set()
    except (Exception, SystemExit):  # noqa: BLE001 - mirrors job_lifecycle
        return False


def _reserve_slot(selection, capacity):
    job = selection.job
    if selection.capacity_mode == 'exclusive':
        return capacity.reserve_exclusive(job.get('id'))
    return capacity.reserve_for_job(job.get('id'), job.get('render_device'))


def _record_claim(job: Dict[str, Any], selection, slot) -> None:
    """Attach the reservations to the job and register it as active."""
    from sethlans_worker_agent import job_processor
    job_id = job.get('id')
    job['_reservation'] = slot
    job['_renderer_reservation'] = selection.renderer_reservation
    job['assigned_gpu_index'] = slot.primary_gpu_index
    entry = {
        'job_id': job_id, 'name': job.get('name', 'Unnamed Job'),
        'render_engine': job.get('render_engine', ''),
        'render_device': job.get('render_device'),
        'gpu_index': slot.primary_gpu_index,
        'device_used': slot.device_used,
        'start_time': datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        'renderer': job.get('renderer') or 'BLENDER',
        'exclusive': selection.capacity_mode == 'exclusive',
    }
    # Lets this claim's releases pop only its own entry (job_resources).
    job[ACTIVE_ENTRY] = entry
    with job_processor._active_jobs_lock:
        job_processor._active_jobs[job_id] = entry
        job_processor._unfinished_job_ids.add(job_id)


def claim_selected(selection, capacity, worker_id) -> Optional[Dict[str, Any]]:
    """Reserve capacity for the selected job and claim it on the manager.

    Returns the job dict (with ``_reservation`` and
    ``_renderer_reservation``) on success, else None.
    """
    job = selection.job
    job_id = job.get('id')
    job_name = job.get('name', 'Unnamed Job')
    claimed = False
    slot = None
    try:
        slot = _reserve_slot(selection, capacity)
        if slot is None:
            logger.info(
                "No compatible slot for job '%s' (%s). Skipping claim.",
                job_name, job.get('render_device'),
            )
            return None
        if _shutdown_requested():
            logger.info("Shutdown in progress; not claiming job %s.", job_id)
            return None
        logger.info(
            f"Attempting to claim job '{job_name}' (ID: {job_id}) "
            f"for device {slot.device_used}."
        )
        if api_handler.claim_job(job_id, worker_id):
            logger.info(f"Successfully claimed job '{job_name}'!")
            try:
                _record_claim(job, selection, slot)
            except Exception:
                logger.critical(
                    "Claimed job %s on the manager but could not record it "
                    "locally; releasing its local resources.", job_id,
                    exc_info=True,
                )
                forget_job(job, unfinished=True)
                raise
            claimed = True
            return job
        return None
    finally:
        if not claimed:
            release_reservations(
                job_id, selection.renderer_reservation,
                capacity if slot is not None else None,
            )
