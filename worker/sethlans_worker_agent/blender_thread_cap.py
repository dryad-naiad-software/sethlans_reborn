# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
CPU thread cap helper for Blender subprocess invocations.

Extracted from blender_executor.py for file size compliance. Implements
the FR-14/FR-15/FR-16 rule: CPU-bound Blender invocations receive
``--threads N`` where N is computed by the capacity module; GPU-bound
invocations do NOT receive ``--threads``.
"""
import logging

from sethlans_worker_agent import capacity as capacity_module

logger = logging.getLogger(__name__)


def maybe_add_threads_flag(command, job_id, assigned_gpu_index):
    """Append ``--threads N`` to command for CPU-bound Blender invocations.

    Gates on the actual assigned slot (``assigned_gpu_index``) rather than
    render_device, because an ANY job that wins a GPU slot still has
    ``render_device='ANY'``. FR-16: GPU jobs must NOT receive ``--threads``.
    """
    if assigned_gpu_index is not None:
        return
    from sethlans_worker_agent import job_processor
    worker_capacity = job_processor.get_worker_capacity()
    if worker_capacity is None:
        return
    try:
        effective = capacity_module.cpu_threads_for_blender(worker_capacity.profile)
    except ValueError as exc:
        logger.error(
            f"[Job {job_id}] cpu_threads_for_blender raised: "
            f"{exc}. Proceeding without --threads."
        )
        return
    command.extend(["--threads", str(effective)])
    logger.info(f"[Job {job_id}] CPU thread cap: --threads {effective}")
