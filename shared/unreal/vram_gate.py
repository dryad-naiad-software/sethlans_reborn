# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
VRAM gate arithmetic (Unreal foundation D2, FR-MU-5, FR-UE-21, FR-RT-9).

Pure arithmetic shared by the worker's gate and the manager's routing
note, so the two cannot drift. A job is eligible on a GPU when::

    need_mb = VRAM_RESERVE_MB + sum(pool caps)
    need_mb <= floor(total_mb * VRAM_BUDGET_FRACTION) - used_mb

The pool caps are **summed, never compared per pool**: the engine may
fill both from the same VRAM. A pool CVar absent from the merged CVars
counts at ``DEFAULT_POOL_MB``.

Constants from spike S6 (spec v4.1): the reserve is ``ceil(1.1 * 8,252)``,
the non-pool peak of theta's full-edit capture; the fraction is the
16 GB card's DXGI budget ratio, which must hold for the smallest
supported card. Whether to keep this gate model is open (OQ-23).
"""
import math

POOL_CVARS = (
    "r.Streaming.PoolSize",
    "r.RayTracing.ResidentGeometryMemoryPoolSizeInMB",
)
DEFAULT_POOL_MB = {
    "r.Streaming.PoolSize": 3000,
    "r.RayTracing.ResidentGeometryMemoryPoolSizeInMB": 400,
}
VRAM_RESERVE_MB = 9078
VRAM_BUDGET_FRACTION = 0.93


def _pool_value_mb(name: str, value) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name}: pool size must be an integer number of MB")
    try:
        megabytes = int(str(value).strip())
    except ValueError:
        raise ValueError(f"{name}: pool size {value!r} is not an integer number of MB") from None
    if megabytes < 0:
        raise ValueError(f"{name}: pool size must not be negative")
    return megabytes


def _lookup(merged_cvars: dict, name: str):
    """Return the value of ``name`` (case-insensitive, as UE reads CVars)."""
    folded = name.casefold()
    matches = [value for key, value in merged_cvars.items() if key.casefold() == folded]
    if len(matches) > 1:
        raise ValueError(f"{name} is set more than once (names are case-insensitive)")
    return matches[0] if matches else None


def vram_need_mb(merged_cvars: dict) -> int:
    """Return ``VRAM_RESERVE_MB`` plus the sum of the pool caps, in MB.

    Raises:
        ValueError: a pool value is not a non-negative integer, or a pool
            CVar is set twice under different letter case.
    """
    total = VRAM_RESERVE_MB
    for name in POOL_CVARS:
        value = _lookup(merged_cvars, name)
        total += DEFAULT_POOL_MB[name] if value is None else _pool_value_mb(name, value)
    return total


def usable_mb(total_mb: int, used_mb: int) -> int:
    """Return ``floor(total_mb * VRAM_BUDGET_FRACTION) - used_mb``."""
    return math.floor(total_mb * VRAM_BUDGET_FRACTION) - used_mb


def fits(need_mb: int, total_mb: int, used_mb: int) -> bool:
    """Return whether ``need_mb`` fits on a GPU of ``total_mb`` with ``used_mb`` in use."""
    return need_mb <= usable_mb(total_mb, used_mb)
