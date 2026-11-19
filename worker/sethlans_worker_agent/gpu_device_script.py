# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Cycles GPU device selection for the generated Blender render script.

Blender's ``prefs.devices`` holds one entry per backend, so one RTX card
appears as a CUDA entry and as an OPTIX entry. The worker works with
physical cards instead: the job's slot reservation carries a copy of the
cards in use (``capacity.SlotReservation.cards``, each with its key, name,
Blender device ``id`` and preferred backend) and the keys it holds (#238,
FR-CB-1). Nothing here reads the detection cache.

The worker's card list is never trusted on its own. The generated script
embeds the cards in use, the cards this job must find, and the ids of the
detected cards that are not in use (``ignored``, FR-CB-2), and in Blender
(``gpu_select_helper.SELECT_HELPER``):

- matches every job card to exactly one device entry by backend and PCI
  address (FR-CB-4, B2), not by the full id, so another Blender version's
  name for the card still finds it; a Metal card is the only ``METAL``
  entry (D9, B3); no entry, or two, fails the job;
- isolated mode (one job card): switches exactly that entry on; combined
  mode: every card in use must have exactly one entry of the chosen
  backend, and those are switched on; every other entry, and every entry
  at an ignored card's address, is switched off;
- re-reads ``prefs.devices`` (and asks ``has_active_device()`` when the
  Blender build has it) to verify the result before rendering;
- reports, without failing, list drift that does not concern this job;
  an ignored card is never reported.

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

from sethlans_worker_agent.cards.in_use import one_family
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
    """Return the preferred backend among ``detected_backends``, or None if none is known."""
    return next((b for b in KNOWN_BACKENDS if b in (detected_backends or [])), None)


def _card_problem(card) -> Optional[str]:
    """Return why a card in use (``CardInUse``) cannot be embedded, or None."""
    if getattr(card, 'backend', None) not in KNOWN_BACKENDS:
        return (f"has unknown compute backend {getattr(card, 'backend', None)!r}; "
                f"expected one of {list(KNOWN_BACKENDS)}")
    if not isinstance(getattr(card, 'blender_id', None), str) or not card.blender_id:
        return "has no Blender device id"
    return None


def _card_tuple(card):
    """Return the (id, type, name) tuple embedded for one valid card."""
    return (card.blender_id, card.backend, str(card.name or 'unknown'))


def _valid_cards(cards, own_keys):
    """Return the embeddable cards in use, warning about and dropping bad ones.

    A bad card among the job's own (``own_keys``) fails the job instead: a
    job given a card must never spread to other cards or to the CPU.
    """
    valid = []
    for card in cards:
        problem = _card_problem(card)
        if problem is None:
            valid.append(_card_tuple(card))
        elif card.key in own_keys:
            raise ValueError(f"Graphics card {card.key} {problem}, so it cannot be selected.")
        else:
            logger.warning(f"Graphics card {card.key} {problem}; leaving it out of the render script.")
    return valid


def gpu_selection(reservation):
    """Return (backend, mode, cards, job_cards, ignored) for a GPU reservation (FR-CB-1, FR-CB-2).

    One job card: isolated mode on that card's backend. Several (combined
    mode): the cards must be of one backend family (FR-CC-4). CUDA and
    OptiX cards together render through CUDA, and every card must be found
    by its CUDA entry; otherwise the preferred backend among them is chosen.
    Raises ValueError when the reservation names no usable card.
    """
    own = reservation.job_cards
    if not own:
        raise ValueError("The job's slot holds no graphics card. Refusing to render on any other device.")
    cards = _valid_cards(reservation.cards, {card.key for card in own})
    if len(own) == 1:
        backend, mode = own[0].backend, 'isolated'
    else:
        if not one_family(card.backend for card in own):
            raise ValueError("The cards of a combined slot are not driven the same way by Blender.")
        backends = {card.backend for card in own}
        # CUDA and OptiX together: CUDA, because every NVIDIA card has a CUDA entry and a CUDA-listed one has no OptiX.
        backend, mode = ('CUDA' if backends == {'CUDA', 'OPTIX'} else choose_backend(backends)), 'combined'
    if mode == 'combined' and backend == 'CUDA':
        job_cards = [(card.blender_id.removesuffix('_OptiX'), 'CUDA') for card in own]
    else:
        job_cards = [(card.blender_id, card.backend) for card in own if card.backend == backend]
    ignored = [device_id for device_id in reservation.ignored if isinstance(device_id, str)]
    return backend, mode, cards, job_cards, ignored


def _append_selection(script_lines, backend, mode, cards, job_cards, ignored=()):
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
    script_lines.append("        [  # every card in use: (id, type, name)")
    for card in cards:
        script_lines.append(f"            {ascii(tuple(card))},")
    script_lines.append("        ],")
    script_lines.append("        [  # the cards that must be found: (id, type)")
    for card in job_cards:
        script_lines.append(f"            {ascii(tuple(card))},")
    script_lines.append("        ],")
    script_lines.append("        [  # detected cards not in use: always off, never reported")
    for device_id in ignored:
        script_lines.append(f"            {ascii(device_id)},")
    script_lines.append("        ],")
    script_lines.append("    )")
    script_lines.append("    bpy.context.scene.cycles.device = 'GPU'")
    script_lines.append("except SystemExit:")
    script_lines.append("    raise")
    script_lines.append("except BaseException as exc:")
    script_lines.append(
        "    _sethlans_gpu_check_exit(f'unexpected error in the GPU device check ({exc!a})')"
    )


def append_gpu_script(script_lines, job_id, reservation):
    """Append script lines that enable exactly the reservation's cards."""
    backend, mode, cards, job_cards, ignored = gpu_selection(reservation)
    if mode == 'isolated':
        logger.info(f"[Job {job_id}] Isolating graphics card {reservation.job_cards[0].key} "
                    f"({backend}, id {job_cards[0][0]!a}).")
    else:
        logger.info(f"[Job {job_id}] Using {len(reservation.job_cards)} graphics cards through {backend} (combined).")
    _append_selection(script_lines, backend, mode, cards, job_cards, ignored)


def log_gpu_assignment(job_id, reservation):
    """Log which graphics cards a job renders on (nothing for a CPU job)."""
    if reservation is None or reservation.device_used != 'GPU':
        return
    for card in reservation.job_cards:
        logger.info(f"[Job {job_id}] Assigning to graphics card {card.key} {card.name!a}.")
