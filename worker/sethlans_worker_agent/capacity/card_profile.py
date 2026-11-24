# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The capacity profile from one card evaluation, and its log lines (FR-CL-2, FR-CK-6).

Used by the start check (``job_processor.init_capacity``) and the rebuild
(``card_check.rebuild_profile``), so both build a profile the same way.
``_logged_problem`` is read and written by the agent-loop thread only.

When graphics cards are found but none is ticked and the method is ``cpu``,
``log_profile`` adds one INFO line saying how to choose a card (#305): at
most once per start and per rebuild, never on the periodic check. A rebuild
takes the card count from the cached card probe only, so the line is left
out when no probe is cached or one is running at that moment.
"""
import dataclasses
import logging

from sethlans_worker_agent.capacity.profile import compute_capacity_profile, log_capacity_profile
from sethlans_worker_agent.cards import in_use
from sethlans_worker_agent.config_compute import ENV_CARDS, ENV_METHOD

logger = logging.getLogger(__name__)

CPU_ONLY_HINT_TEXT = (f"Graphics cards found but none chosen; rendering on the CPU only. Choose GPU (or CPU and "
                      f"GPU) and tick a card in the worker settings or from the manager, or set {ENV_METHOD}=gpu "
                      f"and {ENV_CARDS}.")

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


def detected_card_count(inv=None) -> int:
    """The graphics cards in ``inv``; without one, in the card pages' cached probe (never a new probe, B6).

    0 when there is neither, so no hint is logged on a guess.
    """
    if inv is None:
        from sethlans_worker_agent.web_ui import cards_cache
        probe = cards_cache.cached_only_probe()
        inv = probe.inventory if probe is not None else None
    return len(inv.cards) if inv is not None else 0


def profile_for(section, selection, detected: int = 0):
    """The capacity profile for ``section`` and its cards in use (FR-CL-2).

    ``detected``: the graphics cards found; with method ``cpu`` and no ticked
    card the profile's ``cpu_only_hint`` is set when there is one (#305).
    """
    from sethlans_worker_agent import config, system_monitor
    profile = compute_capacity_profile(
        method=section.method, gpu_mode=section.gpu_mode, cards=selection.cards,
        cpu_cores=system_monitor.get_cpu_thread_count(), cpu_threads_config=config.CPU_THREADS,
        ignored=selection.ignored, problem=selection.problem)
    hint = section.method == 'cpu' and not section.cards and not section.read_failed and detected > 0
    return dataclasses.replace(profile, cpu_only_hint=hint) if hint else profile


def log_problem(problem: str) -> None:
    """The FR-CK-6 problem, logged once per change; waiting for Blender to be installed is INFO, not a fault."""
    global _logged_problem
    if problem == _logged_problem:
        return
    if problem:
        logger.log(logging.INFO if problem == in_use.WAITING_FOR_BLENDER_TEXT else logging.WARNING, "%s", problem)
    elif _logged_problem:
        logger.info("The graphics card problem reported earlier no longer applies.")
    _logged_problem = problem


def log_profile(profile, rebuilt: bool = False) -> None:
    """Log a profile the capacity now uses; its problem once per change."""
    if rebuilt:
        logger.info("The slots were rebuilt from the graphics card choice.")
    log_capacity_profile(dataclasses.replace(profile, problem=''))
    log_problem(profile.problem)
    if profile.cpu_only_hint:
        logger.info("%s", CPU_ONLY_HINT_TEXT)


def reset_for_tests() -> None:
    """Forget the logged problem. Test-only hook."""
    global _logged_problem
    _logged_problem = None
