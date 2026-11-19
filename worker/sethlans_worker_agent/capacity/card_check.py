# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The card checks at start, periodically and for a rebuild (FR-CK-1, FR-CK-3, FR-CL-4).

- ``start_check(section)``: before the first profile is built. OS probe and
  the known Blender list, ``matching.evaluate``, the level-triggered alerts
  (a card missing at start is alerted, C4).
- ``check(capacity)``: the periodic check (``WorkerCapacity.assert_gpu_list_unchanged``).
  OS probe; the uncached Blender detection (``drift.detect_physical_gpus_now``)
  whenever cards are ticked and the method includes GPU, or the profile still
  has Blender cards (from an empty profile it can only bring a card back).
  A failed probe or detection, or an unreadable ``config.json``, changes
  nothing: ``DETECTION_FAILED``, try again later (AC-11, C-20). Otherwise one
  CRITICAL line per newly added alert; then, in a ``finally`` (C5), every job
  on a card that is no longer usable for its renderer gets its own stop
  (``blender_stop.request_card_stop``) and a rebuild is requested when the
  cards in use differ from the profile.
- ``rebuild_profile(capacity)``: the profile a pending rebuild swaps in.

The Blender list a check uses without detecting, and the one a rebuild uses,
is the list of the last trusted fresh detection, else the startup cache: one
list, so a check and the rebuild it requests agree. A detection by a fallback
Blender that lacks a card of the profile is not trusted. Ticked cards are
probed and alerted whatever the method; with no ticked card nothing is
probed (B6). Per-card stops reach Blender jobs only (chunk 7 owns the Unreal
launch). Module state is read and written by the agent-loop thread only; the
only locks taken are ``WorkerCapacity._lock`` (to copy the assignments) and
the store's, never together.
"""
import logging
from typing import Optional

from sethlans_worker_agent.capacity import card_blender, rebuild
from sethlans_worker_agent.capacity.card_profile import log_problem, profile_for, selection_of
from sethlans_worker_agent.capacity.drift import DETECTION_FAILED, adopt_detection_executable, detect_physical_gpus_now
from sethlans_worker_agent.cards import in_use, inventory, matching, os_probe, store
from sethlans_worker_agent.cards import warnings as card_warnings
from sethlans_worker_agent.config_store.io import ConfigLockTimeoutError

logger = logging.getLogger(__name__)

_STOPPED_FOR_BOTH = frozenset({matching.LOST, matching.CHANGED, matching.HELD})
_SKIP = object()
_probe_ever_worked = False  # the OS probe gave a list at least once in this process


def _nothing_to_check(section) -> bool:
    """No ticked card: nothing to probe. Ticked cards are checked whatever the method (Unreal uses them, C-23)."""
    return not section.cards


def raise_alerts(result) -> None:
    """``add_alert`` for every unusable ticked card (FR-CK-3); CRITICAL only for a new one.

    A failed write keeps the alert in memory (``store``, C5) and is reported
    from there; it does not stop the check.
    """
    for card in result.unusable:
        try:
            added = store.add_alert(card.kind, card.key, card.name, card.detail)
        except (ConfigLockTimeoutError, OSError):
            added = True
        if added:
            logger.critical("Graphics card %s (%s) is %s (%s).", ascii(card.name), card.key, card.state,
                            ascii(card.detail))


def jobs_on_unusable_cards(capacity, states) -> list:
    """Job ids holding a card that is not usable for their renderer; retained (negative) owners skipped.

    Blender jobs need ``ok``; the whole-machine (Unreal) job ``ok`` or
    ``blender_lost`` (D19). Unticked or unknown cards stop nothing.
    """
    with capacity._lock:
        held = list(capacity._gpu_assignments.items())
        exclusive = capacity._exclusive_job_id
    return sorted({owner for key, owner in held if owner >= 0 and (
        states.get(key) in _STOPPED_FOR_BOTH or (states.get(key) == matching.BLENDER_LOST and owner != exclusive))})


def _stop_jobs(capacity, states) -> None:
    from sethlans_worker_agent import blender_stop
    for job_id in capacity.jobs_on_unusable_cards(states):
        blender_stop.request_card_stop(job_id)


def _request_if_differs(capacity, section, cards, problem) -> None:
    profile = capacity.profile
    if (tuple(cards), problem, section.method, section.gpu_mode) != \
            (tuple(profile.cards), profile.problem, profile.method, profile.gpu_mode):
        rebuild.request_rebuild()


def _probe():
    """The OS probe; remembers that it worked once in this process."""
    global _probe_ever_worked
    os_cards = os_probe.probe()
    _probe_ever_worked = _probe_ever_worked or os_cards is not None
    return os_cards


def _evaluate_known(section, capacity=None):
    """Evaluate with a fresh OS probe and the known Blender list; alerts raised, jobs stopped (real probe only).

    Returns ``(selection, inventory)``.
    """
    blender_cards, version = card_blender.known()
    inv = inventory.build(_probe(), blender_cards, version)
    result = matching.evaluate(section, inv, profile_keys=capacity.profile.card_keys if capacity else ())
    try:
        raise_alerts(result)
    finally:
        if capacity is not None and not inv.os_probe_failed:
            _stop_jobs(capacity, result.states)
    card_warnings.remember(inv, result)
    return selection_of(result, blender_cards), inv


def _no_tick_selection(section) -> in_use.Selection:
    card_warnings.forget()  # nothing was probed: the Eevee/Workbench condition is evaluated again when needed
    return in_use.Selection(problem=in_use.problem_message(section, ()))


def start_check(section) -> in_use.Selection:
    """FR-CK-1 at start; an unreadable ``config.json`` keeps the defaults (CPU only) and says so (C-20)."""
    if section.read_failed:
        logger.error("The graphics card settings could not be read at start; the worker starts on the CPU only "
                     "and uses the saved choice once it can be read.")
        return in_use.Selection()
    if _nothing_to_check(section):
        return _no_tick_selection(section)
    return _evaluate_known(section)[0]


def rebuild_profile(capacity):
    """The profile for a pending rebuild, or ``None`` to stay pending.

    ``None`` when ``config.json`` cannot be read, and when the OS probe failed
    although it worked before in this process (a ticked card would read
    ``unknown`` and drop out). A machine whose probe never worked builds from
    the Blender fallback, as at start.
    """
    section = store.snapshot()
    if section.read_failed:
        return None
    if _nothing_to_check(section):
        return profile_for(section, _no_tick_selection(section))
    selection, inv = _evaluate_known(section, capacity)
    if inv.os_probe_failed and _probe_ever_worked:
        return None
    return profile_for(section, selection)


def _profile_offered(inv, profile) -> bool:
    by_key = {card.key: card for card in inv.cards}
    return all(key in by_key and by_key[key].blender is not None for key in profile.card_keys)


def check(capacity) -> Optional[str]:
    """The periodic check; ``DETECTION_FAILED`` when nothing could be decided, else ``None``."""
    profile = capacity.profile
    section = store.snapshot()
    if section.read_failed:
        return DETECTION_FAILED
    if _nothing_to_check(section) and not profile.cards:
        card_warnings.forget()
        _request_if_differs(capacity, section, (), in_use.problem_message(section, ()))
        return None
    os_cards = _probe()
    detect = os_cards is not None and (section.method != 'cpu' or profile.cards) and section.cards
    fresh = detect_physical_gpus_now() if detect else _SKIP
    section = store.snapshot()  # after the detection (seconds): the alerts as they are now
    if os_cards is None or fresh is None or section.read_failed:
        return DETECTION_FAILED
    blender_cards, version, is_fresh = (*card_blender.known(), False) if fresh is _SKIP else \
        card_blender.from_detection(fresh, os_cards, profile)
    inv = inventory.build(os_cards, blender_cards, version)
    result = matching.evaluate(section, inv, blender_fresh=is_fresh, profile_keys=profile.card_keys)
    try:
        raise_alerts(result)
    finally:  # C5: even when an alert could not be raised
        _stop_jobs(capacity, result.states)
        _request_if_differs(capacity, result.section, result.blender_cards, result.problem)
    card_warnings.remember(inv, result)
    log_problem(result.problem)
    if is_fresh and _profile_offered(inv, profile):
        adopt_detection_executable()  # a replacement detection Blender agreed (#253)
    return None


def reset_for_tests() -> None:
    """Forget the last fresh Blender list, the probe record and the logged problem. Test-only hook."""
    global _probe_ever_worked
    _probe_ever_worked = False
    card_blender.reset_for_tests()
    from sethlans_worker_agent.capacity import card_profile
    card_profile.reset_for_tests()
