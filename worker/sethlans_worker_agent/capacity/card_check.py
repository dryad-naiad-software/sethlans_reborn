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
  (``card_stops``) and a rebuild is requested when the cards in use differ
  from the profile.
- ``check_during_unreal(capacity, now)``: while any Unreal job holds a card
  ``check`` never runs (FR-CK-1, render per card D15). Instead, once per
  heartbeat interval, the OS list alone (DXGI and D3DKMT reads; no device is
  created, Blender is never started) with the known Blender list: the same
  alerts, the stop of the jobs whose card is lost, changed or held (C-24 m),
  and a rebuild request (it waits only while a running job holds a card the
  new profile drops).

Every check also reads the screen marks (``cards.screens.marked``: DXGI
outputs or DRM connector files, no device created, so also during an Unreal
hold) and logs a new ``ticked_drives_screen`` warning once (FR-CS-2, FR-CS-3).
Marks change no state, slot or tick.
- ``rebuild_profile(capacity)``: the profile a pending rebuild swaps in.

The Blender list a check uses without detecting, and the one a rebuild uses,
is the list of the last trusted fresh detection, else the startup cache: one
list, so a check and the rebuild it requests agree. A detection by a fallback
Blender that lacks a card of the profile is not trusted. Ticked cards are
probed and alerted whatever the method; with no ticked card nothing is
probed (B6). Module state is read and written by the agent-loop thread only; the
only locks taken are ``WorkerCapacity._lock`` (to copy the assignments) and
the store's, never together.
"""
import logging
from typing import Optional

from sethlans_worker_agent import config
from sethlans_worker_agent.capacity import card_blender, rebuild
from sethlans_worker_agent.capacity.card_profile import detected_card_count, log_problem, profile_for, selection_of
from sethlans_worker_agent.capacity.card_stops import raise_alerts, stop_jobs
from sethlans_worker_agent.capacity.drift import DETECTION_FAILED, adopt_detection_executable, detect_physical_gpus_now
from sethlans_worker_agent.cards import in_use, inventory, matching, os_probe, screens, store
from sethlans_worker_agent.cards import warnings as card_warnings

logger = logging.getLogger(__name__)

_SKIP = object()
_probe_ever_worked = False  # the OS probe gave a list at least once in this process
_last_unreal_check: Optional[float] = None  # monotonic time of the last check during an Unreal hold


def _nothing_to_check(section) -> bool:
    """No ticked card: nothing to probe. Ticked cards are checked whatever the method (Unreal uses them, C-23)."""
    return not section.cards


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
    inv = screens.marked(inventory.build(_probe(), blender_cards, version))
    result = matching.evaluate(section, inv, profile_keys=capacity.profile.card_keys if capacity else ())
    try:
        raise_alerts(result)
    finally:
        if capacity is not None and not inv.os_probe_failed:
            stop_jobs(capacity, result.states)
    card_warnings.remember(inv, result)
    card_warnings.log_screen_warnings(inv, result)
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
    if _nothing_to_check(section):  # the cached card probe only says whether cards were found (#305; B6)
        return profile_for(section, _no_tick_selection(section), detected=detected_card_count())
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
    inv = screens.marked(inventory.build(os_cards, blender_cards, version))
    result = matching.evaluate(section, inv, blender_fresh=is_fresh, profile_keys=profile.card_keys)
    try:
        raise_alerts(result)
    finally:  # C5: even when an alert could not be raised
        stop_jobs(capacity, result.states)
        _request_if_differs(capacity, result.section, result.blender_cards, result.problem)
    card_warnings.remember(inv, result)
    card_warnings.log_screen_warnings(inv, result)
    log_problem(result.problem)
    if is_fresh and _profile_offered(inv, profile):
        adopt_detection_executable()  # a replacement detection Blender agreed (#253)
    return None


def check_during_unreal(capacity, now: float) -> None:
    """C-24 m: once per heartbeat interval during an Unreal hold, the OS list only (no Blender is started).

    The level-triggered alerts, the stop of the Unreal job whose card is
    gone, and a rebuild request (it waits for the hold to end). An unreadable
    ``config.json`` or a failed probe changes nothing.
    """
    global _last_unreal_check
    if _last_unreal_check is not None and now - _last_unreal_check < max(1, config.HEARTBEAT_INTERVAL_SECONDS):
        return
    _last_unreal_check = now
    section = store.snapshot()
    if section.read_failed or _nothing_to_check(section):
        return
    selection, inv = _evaluate_known(section, capacity)
    if not inv.os_probe_failed:
        _request_if_differs(capacity, section, selection.cards, selection.problem)


def reset_for_tests() -> None:
    """Forget the last fresh Blender list, the probe record and the logged problem. Test-only hook."""
    global _probe_ever_worked, _last_unreal_check
    _probe_ever_worked, _last_unreal_check = False, None
    card_blender.reset_for_tests()
    from sethlans_worker_agent.capacity import card_profile
    card_profile.reset_for_tests()
