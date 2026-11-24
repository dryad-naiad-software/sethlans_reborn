# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Pure capacity formula and CPU thread cap.

Contains dataclasses (CapacityProfile, SlotReservation), the pure
compute_capacity_profile() function, the cpu_threads_for_blender()
formula with the FR-15 defensive assertion, and log_capacity_profile().

This module has no threading or subprocess dependencies.
"""
import logging
from dataclasses import dataclass
from typing import Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CapacityProfile:
    """Frozen result of the capacity computation (FR-CL-2).

    ``cards`` are the Blender cards in use, ``cards.in_use.CardInUse``
    tuples ``(key, name, blender_id, backend)`` in PCI address order (D4);
    GPU slots are keyed by card key. ``ignored`` are the Blender ids of the
    detected cards that are not in use (FR-CB-2). ``problem`` is the FR-CK-6
    text when the method includes GPU but no card is in use. ``cpu_only_hint``
    is true when the method is ``cpu``, no card is ticked and graphics cards
    were found (#305): the profile log then says how to choose one. It is log
    data only and changes no slot.
    """
    gpu_slot_count: int
    cpu_slot_count: int
    cpu_thread_ceiling: int   # cores - 1 (>= 1)
    cpu_threads_effective: int  # min(CPU_THREADS or ceiling, ceiling)
    gpu_mode: str  # "split" or "combined"
    method: str = 'cpu'  # "cpu", "gpu" or "cpu_gpu"
    cards: tuple = ()
    ignored: Tuple[str, ...] = ()
    problem: str = ''
    cpu_only_hint: bool = False

    @property
    def total(self) -> int:
        return self.gpu_slot_count + self.cpu_slot_count

    @property
    def card_keys(self) -> Tuple[str, ...]:
        return tuple(card.key for card in self.cards)


@dataclass(frozen=True)
class SlotReservation:
    """Handle returned by reserve_for_job (FR-CL-3).

    A GPU reservation copies, under ``WorkerCapacity._lock`` at claim, the
    cards in use from the profile (``cards``) and the keys it holds
    (``card_keys``: one in split mode, every card in combined mode). The
    executor, the render script and the thumbnail pass read only this copy,
    never the profile, so a later profile cannot move a running job to
    another card. ``device_used`` decides CPU or GPU (FR-CB-1).
    """
    job_id: int
    device_used: str  # "GPU" or "CPU"
    card_keys: Tuple[str, ...] = ()
    cards: tuple = ()
    gpu_mode: str = 'split'
    ignored: Tuple[str, ...] = ()
    problem: str = ''

    @property
    def job_cards(self) -> tuple:
        """The cards this reservation renders on, in address order."""
        return tuple(card for card in self.cards if card.key in self.card_keys)


def compute_capacity_profile(
    *,
    method: str,
    gpu_mode: str,
    cards: tuple,
    cpu_cores: int,
    cpu_threads_config: int,
    ignored: Tuple[str, ...] = (),
    problem: str = '',
) -> CapacityProfile:
    """Pure function (FR-2, FR-CL-2)."""
    # CPU slot (FR-2); none when the method is GPU only.
    cpu_usable = (cpu_cores >= 2) and method != 'gpu'
    cpu_slot_count = 1 if cpu_usable else 0

    # GPU slots: one per card in use (split), one for all of them (combined).
    if method == 'cpu':
        gpu_slot_count = 0
    elif gpu_mode == "combined":
        gpu_slot_count = 1 if cards else 0
    else:  # split (default)
        gpu_slot_count = len(cards)

    # CPU thread ceiling. max(1, cores - 1) keeps the ceiling well-defined
    # on 1-core machines; FR-2 prevents a CPU slot there anyway.
    ceiling = max(1, cpu_cores - 1)
    threads_requested = cpu_threads_config if cpu_threads_config > 0 else ceiling
    effective = min(threads_requested, ceiling)

    in_use = tuple(cards) if method != 'cpu' else ()
    return CapacityProfile(
        gpu_slot_count=gpu_slot_count,
        cpu_slot_count=cpu_slot_count,
        cpu_thread_ceiling=ceiling,
        cpu_threads_effective=effective,
        gpu_mode=gpu_mode,
        method=method,
        cards=in_use,
        ignored=tuple(ignored),
        problem=problem,
    )


def cpu_threads_for_blender(profile: CapacityProfile) -> int:
    """Return the --threads value for CPU-bound Blender invocations.

    FR-15: raises ValueError if the effective thread count is less
    than 1. FR-2 guarantees this branch is unreachable; the assertion
    is a belt-and-suspenders guard against future refactors.
    """
    effective = profile.cpu_threads_effective
    if effective < 1:
        raise ValueError(
            f"cpu_threads_for_blender computed {effective}; "
            "FR-2 should have prevented a CPU slot on this host."
        )
    if profile.cpu_threads_effective < profile.cpu_thread_ceiling:
        logger.debug(
            "CPU thread cap honored below ceiling: effective=%d ceiling=%d",
            effective, profile.cpu_thread_ceiling,
        )
    return effective


def log_capacity_profile(profile: CapacityProfile) -> None:
    """Log the resolved capacity profile at INFO (FR-5), and the FR-CK-6 problem at WARNING."""
    logger.info(
        "Worker capacity profile: method=%s gpu_mode=%s cards=%s "
        "gpu_slots=%d cpu_slots=%d total=%d cpu_thread_ceiling=%d "
        "cpu_threads_effective=%d",
        profile.method, profile.gpu_mode, list(profile.card_keys),
        profile.gpu_slot_count, profile.cpu_slot_count, profile.total,
        profile.cpu_thread_ceiling, profile.cpu_threads_effective,
    )
    if profile.problem:
        logger.warning("%s", profile.problem)
