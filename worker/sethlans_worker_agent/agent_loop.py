# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Main-loop helpers for the Sethlans Reborn Worker Agent.

Contains the polling-skip checks, registration helper, and per-iteration
work pump that drive ``agent.main``. Split out of ``agent.py`` to keep
that file under the 300-line cap.

Shared state (``_shutdown_event``, ``_active_threads``,
``_active_threads_lock``) and the ``_should_skip_polling`` callable are
resolved through the ``agent`` module namespace at call-time so test
patches that target ``sethlans_worker_agent.agent.*`` continue to work
without modification.
"""

import logging

from sethlans_worker_agent import (
    api_handler, config, job_processor, system_monitor, version_sync,
)

logger = logging.getLogger(__name__)


def _should_skip_polling():
    """Check if job polling should be skipped this cycle. Returns wait time or None."""
    if job_processor.is_paused():
        logger.debug("Worker is paused. Skipping job poll.")
        return config.JOB_POLLING_INTERVAL_SECONDS
    if api_handler.is_auth_failed():
        logger.warning("Authentication failed. Skipping job poll. Heartbeats will continue.")
        return config.HEARTBEAT_INTERVAL_SECONDS
    if not system_monitor.are_versions_ready():
        logger.warning("No required Blender versions installed yet. Skipping job poll.")
        return config.HEARTBEAT_INTERVAL_SECONDS

    # Schedule gate (FR-8): check if we are inside the claim window.
    from sethlans_worker_agent.idle_detection import schedule
    window_cfg = config.get_schedule_config()
    if not schedule.is_inside_claim_window(window_cfg):
        logger.debug("Outside claim window. Skipping job poll.")
        return config.JOB_POLLING_INTERVAL_SECONDS

    # Idle gate (FR-1, FR-2): check if the machine is idle.
    from sethlans_worker_agent.idle_detection import is_idle
    overrides = window_cfg.get('overrides_idle_detection', False)
    in_window = schedule.is_inside_claim_window(window_cfg)
    skip_idle = overrides and in_window
    if not skip_idle and not is_idle():
        logger.debug("Machine is not idle. Skipping job poll.")
        return config.JOB_POLLING_INTERVAL_SECONDS

    return None


def _try_register_worker():
    """Attempt registration. Returns the worker ID on success, else None."""
    logger.warning("Worker not registered with Manager. Attempting registration...")
    new_id = system_monitor.register_with_manager()
    if not new_id:
        logger.error("Failed to register with manager. Retrying in 30 seconds...")
        return None
    job_processor.init_capacity()
    return new_id


def _run_loop_iteration(worker_id):
    """One iteration of the main loop after the worker is registered.

    Sends the heartbeat, runs the drift check, processes pending
    downloads, then either polls for and dispatches a job or skips
    polling (paused / auth fail / versions not ready / capacity full).
    """
    # Resolve shared state through the agent module so test patches
    # targeting ``sethlans_worker_agent.agent.*`` are honoured.
    from sethlans_worker_agent import agent

    active_jobs = job_processor.get_active_jobs_snapshot()
    is_busy = len(active_jobs) > 0

    # Heartbeats run regardless of pause state.
    system_monitor.send_heartbeat(is_busy=is_busy, active_jobs=active_jobs)

    # GPU drift check (FR-22, FR-23). Fires at most once per heartbeat
    # interval, not once per poll interval.
    job_processor.maybe_assert_gpu_count_unchanged()

    from sethlans_worker_agent.agent_setup import check_manager_setup_complete
    if not check_manager_setup_complete():  # issue #126: skip work until setup done
        agent._shutdown_event.wait(config.HEARTBEAT_INTERVAL_SECONDS)
        return

    if not is_busy:
        version_sync.process_pending_downloads()
        version_sync.process_pending_removals(active_jobs)

    skip_wait = agent._should_skip_polling()
    if skip_wait is not None:
        agent._shutdown_event.wait(skip_wait)
        return

    # Capacity gate (FR-6). Source of truth is WorkerCapacity.is_full(),
    # NOT _active_jobs.
    if job_processor.capacity_is_full():
        logger.debug("Worker at capacity. Skipping poll.")
        agent._shutdown_event.wait(config.JOB_POLLING_INTERVAL_SECONDS)
        return

    thread = job_processor.get_and_claim_job(worker_id)
    if thread is not None:
        with agent._active_threads_lock:
            agent._active_threads.append(thread)

    logger.debug(f"Loop finished. Sleeping for {config.JOB_POLLING_INTERVAL_SECONDS} seconds.")
    agent._shutdown_event.wait(config.JOB_POLLING_INTERVAL_SECONDS)
