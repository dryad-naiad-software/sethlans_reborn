# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The capacity profile from one card evaluation, and its log lines (FR-CL-2, FR-CK-6).

Used by the start check (``job_processor.init_capacity``) and the rebuild
(``card_check.rebuild_profile``), so both build a profile the same way.
``_logged_problem`` is read and written by the agent-loop thread only.
"""
import dataclasses
import logging

from sethlans_worker_agent.capacity.profile import compute_capacity_profile, log_capacity_profile
from sethlans_worker_agent.cards import in_use

logger = logging.getLogger(__name__)

_logged_problem = None  # the FR-CK-6 text last logged


def selection_of(result, blender_cards) -> in_use.Selection:
    """The cards in use, the ignored Blender ids and the problem of one evaluation.

    With Combine GPUs over two families nothing is in use and nothing is ignored.
    """
    if result.problem == in_use.COMBINE_PROBLEM_TEXT:
        return in_use.Selection(problem=result.problem)
    used = {card.blender_id for card in result.blender_cards}
    detected = [record.get('id') for record in blender_cards if isinstance(record, dict)] \
        if isinstance(blender_cards, list) else []
    ignored = tuple(device for device in detected if isinstance(device, str) and device not in used)
    return in_use.Selection(cards=result.blender_cards, ignored=ignored, problem=result.problem)


def profile_for(section, selection):
    """The capacity profile for ``section`` and its cards in use (FR-CL-2)."""
    from sethlans_worker_agent import config, system_monitor
    return compute_capacity_profile(
        method=section.method, gpu_mode=section.gpu_mode, cards=selection.cards,
        cpu_cores=system_monitor.get_cpu_thread_count(), cpu_threads_config=config.CPU_THREADS,
        ignored=selection.ignored, problem=selection.problem)


def log_problem(problem: str) -> None:
    """The FR-CK-6 problem, logged once per change."""
    global _logged_problem
    if problem == _logged_problem:
        return
    if problem:
        logger.warning("%s", problem)
    elif _logged_problem:
        logger.info("The graphics card problem reported earlier no longer applies.")
    _logged_problem = problem


def log_profile(profile, rebuilt: bool = False) -> None:
    """Log a profile the capacity now uses; its problem once per change."""
    if rebuilt:
        logger.info("The slots were rebuilt from the graphics card choice.")
    log_capacity_profile(dataclasses.replace(profile, problem=''))
    log_problem(profile.problem)


def reset_for_tests() -> None:
    """Forget the logged problem. Test-only hook."""
    global _logged_problem
    _logged_problem = None
