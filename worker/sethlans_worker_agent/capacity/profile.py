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
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CapacityProfile:
    """Frozen result of the startup capacity computation."""
    gpu_slot_count: int
    cpu_slot_count: int
    cpu_thread_ceiling: int   # cores - 1 (>= 1)
    cpu_threads_effective: int  # min(CPU_THREADS or ceiling, ceiling)
    startup_gpu_count: int
    gpu_mode: str  # "split" or "combined"
    force_gpu_index: Optional[int] = None

    @property
    def total(self) -> int:
        return self.gpu_slot_count + self.cpu_slot_count


@dataclass
class SlotReservation:
    """Handle returned by reserve_for_job.

    Contains everything the caller needs to drive blender_executor and
    to release on failure. The gpu_indices list uses Blender's
    non-CPU device ordering (FR-20a).
    """
    job_id: int
    device_used: str  # "GPU" or "CPU"
    gpu_indices: List[int] = field(default_factory=list)

    @property
    def primary_gpu_index(self) -> Optional[int]:
        """Return the first GPU index, or None for CPU / combined mode.

        For split mode this is the single index to isolate via
        render_script._append_gpu_isolation_script. For combined mode
        this is None, which signals render_script._append_cycles_gpu_config
        to enable all non-CPU devices via its default branch.
        """
        if len(self.gpu_indices) == 1:
            return self.gpu_indices[0]
        return None


def compute_capacity_profile(
    *,
    detected_gpu_count: int,
    cpu_cores: int,
    force_cpu_only: bool,
    force_gpu_only: bool,
    force_gpu_index: Optional[int],
    gpu_mode: str,
    cpu_threads_config: int,
) -> CapacityProfile:
    """Pure function. Implements FR-2/FR-3 verbatim."""
    # CPU slot (FR-2)
    cpu_usable = (cpu_cores >= 2) and not force_gpu_only
    cpu_slot_count = 1 if cpu_usable else 0

    # GPU slots (FR-3)
    if force_cpu_only:
        gpu_slot_count = 0
    elif force_gpu_index is not None:
        gpu_slot_count = 1
    elif gpu_mode == "combined":
        gpu_slot_count = 1 if detected_gpu_count > 0 else 0
    else:  # split (default)
        gpu_slot_count = detected_gpu_count

    # CPU thread ceiling. max(1, cores - 1) keeps the ceiling well-defined
    # on 1-core machines; FR-2 prevents a CPU slot there anyway.
    ceiling = max(1, cpu_cores - 1)
    threads_requested = cpu_threads_config if cpu_threads_config > 0 else ceiling
    effective = min(threads_requested, ceiling)

    return CapacityProfile(
        gpu_slot_count=gpu_slot_count,
        cpu_slot_count=cpu_slot_count,
        cpu_thread_ceiling=ceiling,
        cpu_threads_effective=effective,
        startup_gpu_count=detected_gpu_count,
        gpu_mode=gpu_mode,
        force_gpu_index=force_gpu_index,
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


def log_capacity_profile(
    profile: CapacityProfile,
    detected_gpu_count: int,
    force_cpu_only: bool,
    force_gpu_only: bool,
) -> None:
    """Log the resolved capacity profile at INFO (FR-5)."""
    logger.info(
        "Worker capacity profile: gpus_detected=%d gpu_mode=%s "
        "force_cpu_only=%s force_gpu_only=%s force_gpu_index=%s "
        "gpu_slots=%d cpu_slots=%d total=%d cpu_thread_ceiling=%d "
        "cpu_threads_effective=%d",
        detected_gpu_count, profile.gpu_mode,
        force_cpu_only, force_gpu_only, profile.force_gpu_index,
        profile.gpu_slot_count, profile.cpu_slot_count, profile.total,
        profile.cpu_thread_ceiling, profile.cpu_threads_effective,
    )
