# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Cycles GPU device selection for the generated Blender render script.

Blender's ``prefs.devices`` holds one entry per backend, so one RTX card
appears as a CUDA entry and as an OPTIX entry. The worker's GPU index
counts physical cards instead: it is a position in
``system_monitor.get_gpu_device_details()``, one record per card carrying
that card's preferred backend (``type``) and Blender's device ``id`` for
that backend's entry (#238).

The worker's card list is never trusted on its own. The generated script
embeds the whole list plus the cards this job must find, and in Blender
(``gpu_select_helper.SELECT_HELPER``):

- matches every such card to exactly one device entry by ``id`` AND
  ``type``, failing the job if one is missing;
- isolated mode: switches exactly that entry on; combined mode: switches
  on every entry of the chosen backend that belongs to a listed card;
  every other entry is switched off;
- re-reads ``prefs.devices`` (and asks ``has_active_device()`` when the
  Blender build has it) to verify the result before rendering;
- reports, without failing, list drift that does not concern this job.

A failure, including any unexpected error in that code, writes one
``GPU_CHECK_MARKER`` line to stderr and exits with ``GPU_CHECK_EXIT_CODE``
before the render starts, so a GPU job never falls back to the CPU
silently. Drift lines start with ``GPU_LIST_MISMATCH_MARKER`` and go to
stderr, which ``blender_executor`` logs at WARNING. The job error messages
built from the marker (and from a failed settings script, #240) live in
``blender_failure.py``.

Every value embedded in the script goes through ``ascii()``, so the
script is pure ASCII whatever the device names contain.
"""
import logging
from typing import Optional

from sethlans_worker_agent import system_monitor
from sethlans_worker_agent.gpu_select_helper import KNOWN_BACKENDS, SELECT_HELPER

logger = logging.getLogger(__name__)

# Exit code of the generated script when the device check fails. Blender
# itself exits with 0, 1 or a signal-derived code, never with this one.
GPU_CHECK_EXIT_CODE = 87

# Prefix of the single stderr line written when the device check fails.
GPU_CHECK_MARKER = 'SETHLANS_GPU_CHECK_FAILED:'

# Prefix of each stderr line reporting card-list drift that does not
# concern the job's own cards (the job still renders).
GPU_LIST_MISMATCH_MARKER = 'SETHLANS_GPU_LIST_MISMATCH:'


def choose_backend(detected_backends) -> Optional[str]:
    """Return the machine-wide preferred backend, or None if none is known."""
    return next((b for b in KNOWN_BACKENDS if b in (detected_backends or [])), None)


def _card_problem(card) -> Optional[str]:
    """Return why a detected card record cannot be embedded, or None."""
    if not isinstance(card, dict):
        return "is not a device record"
    if card.get('type') not in KNOWN_BACKENDS:
        return (f"has unknown compute backend {card.get('type')!r}; "
                f"expected one of {list(KNOWN_BACKENDS)}")
    if not isinstance(card.get('id'), str) or not card.get('id'):
        return "has no Blender device id"
    return None


def _card_tuple(card):
    """Return the (id, type, name) tuple embedded for one valid card."""
    return (card['id'], card['type'], str(card.get('name', 'unknown')))


def _valid_cards(cards, keep_index=None):
    """Return the embeddable cards, warning about and dropping bad ones.

    The card at ``keep_index`` (the job's own card) is validated by the
    caller, which fails the job instead.
    """
    valid = []
    for index, card in enumerate(cards):
        problem = _card_problem(card)
        if problem is None:
            valid.append(_card_tuple(card))
        elif index != keep_index:
            logger.warning(f"Detected GPU {index} {problem}; leaving it out of the render script.")
    return valid


def isolated_selection(target_index):
    """Return (backend, cards, job_cards) for one physical card.

    Raises ValueError when the index does not name a usable card: a job
    given one card must never spread to other cards or to the CPU.
    """
    cards = system_monitor.get_gpu_device_details()
    if not isinstance(target_index, int) or not 0 <= target_index < len(cards):
        raise ValueError(
            f"GPU index {target_index!r} is out of range: the worker detected "
            f"{len(cards)} GPU(s). Refusing to render on any other device."
        )
    problem = _card_problem(cards[target_index])
    if problem is not None:
        raise ValueError(f"GPU index {target_index} {problem}, so it cannot be selected.")
    card_id, backend, _name = _card_tuple(cards[target_index])
    return backend, _valid_cards(cards, target_index), [(card_id, backend)]


def combined_selection(backend):
    """Return (backend, cards, job_cards) for combined mode.

    job_cards are the cards listed under ``backend``; each must be found in
    Blender. The script also enables that backend's entries of the other
    listed cards (for example the OptiX entry of a card listed under CUDA).
    """
    if backend not in KNOWN_BACKENDS:
        raise ValueError(f"Unknown compute backend: {backend!r}")
    cards = _valid_cards(system_monitor.get_gpu_device_details())
    job_cards = [(i, t) for i, t, _n in cards if t == backend]
    if not job_cards:
        raise ValueError(f"No detected GPU uses compute backend {backend}.")
    return backend, cards, job_cards


def _append_selection(script_lines, backend, mode, cards, job_cards):
    """Append the helper and its fail-closed call with ascii() data.

    Also switches the scene to GPU rendering inside the same guard, so any
    unexpected error before the render ends the job with the check exit.
    """
    if backend not in KNOWN_BACKENDS or mode not in ('isolated', 'combined'):
        raise ValueError(f"Invalid GPU selection: {backend!r}, {mode!r}")
    script_lines.append("import sys")
    script_lines.append(f"_SETHLANS_GPU_CHECK_MARKER = {ascii(GPU_CHECK_MARKER)}")
    script_lines.append(f"_SETHLANS_GPU_LIST_MISMATCH_MARKER = {ascii(GPU_LIST_MISMATCH_MARKER)}")
    script_lines.append(f"_SETHLANS_GPU_CHECK_EXIT_CODE = {int(GPU_CHECK_EXIT_CODE)}")
    script_lines.append(SELECT_HELPER)
    script_lines.append("try:")
    script_lines.append("    _sethlans_select_gpus(")
    script_lines.append("        bpy.context.preferences.addons['cycles'].preferences,")
    script_lines.append(f"        {ascii(backend)}, {ascii(mode)},")
    script_lines.append("        [  # every card on the worker list: (id, type, name)")
    for card in cards:
        script_lines.append(f"            {ascii(tuple(card))},")
    script_lines.append("        ],")
    script_lines.append("        [  # the cards that must be found: (id, type)")
    for card in job_cards:
        script_lines.append(f"            {ascii(tuple(card))},")
    script_lines.append("        ],")
    script_lines.append("    )")
    script_lines.append("    bpy.context.scene.cycles.device = 'GPU'")
    script_lines.append("except SystemExit:")
    script_lines.append("    raise")
    script_lines.append("except BaseException as exc:")
    script_lines.append(
        "    _sethlans_gpu_check_exit(f'unexpected error in the GPU device check ({exc!a})')"
    )


def append_isolated_gpu_script(script_lines, job_id, target_index):
    """Append script lines that enable exactly one physical card."""
    backend, cards, job_cards = isolated_selection(target_index)
    logger.info(
        f"[Job {job_id}] Isolating physical GPU {target_index} "
        f"({backend}, id {job_cards[0][0]!a})."
    )
    _append_selection(script_lines, backend, 'isolated', cards, job_cards)


def append_combined_gpu_script(script_lines, job_id, backend):
    """Append script lines that enable every listed card the backend drives."""
    backend, cards, job_cards = combined_selection(backend)
    logger.info(f"[Job {job_id}] Using all {len(job_cards)} {backend} GPU(s) (combined).")
    _append_selection(script_lines, backend, 'combined', cards, job_cards)


def log_gpu_assignment(job_id, gpu_index):
    """Log which physical GPU a job is assigned to (None: no index)."""
    if gpu_index is None:
        return
    gpus = system_monitor.get_gpu_device_details()
    if 0 <= gpu_index < len(gpus):
        logger.info(f"[Job {job_id}] Assigning to [Physical GPU {gpu_index}] {gpus[gpu_index].get('name', 'N/A')}.")
    else:
        logger.warning(
            f"[Job {job_id}] GPU index {gpu_index} is out of range ({len(gpus)} GPU(s) detected); "
            "a GPU render on this index will fail instead of using another device."
        )
