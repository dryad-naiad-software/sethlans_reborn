# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
VRAM gate arithmetic (Unreal per-machine memory test D6, D7; Unreal
foundation FR-MU-5).

Pure arithmetic shared by the worker's gate and the manager, so the two
cannot drift. A render or audio job fits a card when its machine's own
measured result peak (that card kind's current ``fits`` result on that
worker, D7) is under the card's 93% line, with no margin::

    peak_mb <= floor(total_mb * VRAM_BUDGET_FRACTION) - used_mb

A pilot has no memory need (S2). The pilot's verdict (D6) uses the same
line: ``fits_result(peak_mb, total_mb, baseline_used_mb)`` is false exactly
when ``peak_mb + baseline_used_mb > floor(total_mb * 0.93)``.

``POOL_CVARS`` names the two pool CVars the worker refuses to see changed
at render time (``cvars.check_effective``); the manager accepts them only
as non-negative integer MB (``renderers.unreal_validation``).
"""
import math

POOL_CVARS = (
    "r.Streaming.PoolSize",
    "r.RayTracing.ResidentGeometryMemoryPoolSizeInMB",
)
VRAM_BUDGET_FRACTION = 0.93
# The smallest plausible pilot peak: an editor-mode UE launch with an RHI
# uses 2.5 GB or more (S1), so less means the measurement missed the render.
MIN_PLAUSIBLE_PEAK_MB = 1024
MAX_PEAK_MB = 1_048_576


def usable_mb(total_mb: int, used_mb: int) -> int:
    """Return ``floor(total_mb * VRAM_BUDGET_FRACTION) - used_mb``."""
    return math.floor(total_mb * VRAM_BUDGET_FRACTION) - used_mb


def fits(need_mb: int, total_mb: int, used_mb: int) -> bool:
    """Return whether ``need_mb`` fits on a GPU of ``total_mb`` with ``used_mb`` in use."""
    return need_mb <= usable_mb(total_mb, used_mb)


def fits_result(peak_mb: int, total_mb: int, used_mb: int) -> bool:
    """D7: whether a result peak fits a card: ``peak_mb <= floor(total_mb * 0.93) - used_mb``.

    Also D6's pilot rule with ``used_mb`` = the pilot's baseline: false exactly when
    ``peak_mb + baseline_used_mb > floor(total_mb * 0.93)``.
    """
    return peak_mb <= usable_mb(total_mb, used_mb)
