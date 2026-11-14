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


# --- Measured-peak gate (v4.3 D2, chunk 2b) ----------------------------
# Normal jobs are gated on a project's measured peak (from its pilot
# render); pilots keep a reserve-based gate (OQ-28 option (d)).
MEASURED_PEAK_MARGIN = 1.15
MEASURED_PEAK_FLOOR_MB = 512
# The smallest plausible pilot peak: an editor-mode UE launch with an RHI
# uses 2.5 GB or more (S1), so less means the measurement missed the render.
MIN_PLAUSIBLE_PEAK_MB = 1024
MAX_PEAK_MB = 1_048_576


def _pool_caps(merged_cvars: dict) -> dict:
    """Return ``{pool CVar: cap MB}``, ``DEFAULT_POOL_MB`` for an absent pool."""
    caps = {}
    for name in POOL_CVARS:
        value = _lookup(merged_cvars, name)
        caps[name] = DEFAULT_POOL_MB[name] if value is None else _pool_value_mb(name, value)
    return caps


def caps_sum_mb(merged_cvars: dict) -> int:
    """Return the sum of the pool caps (``DEFAULT_POOL_MB`` for an absent pool).

    Raises:
        ValueError: as :func:`vram_need_mb`.
    """
    return sum(_pool_caps(merged_cvars).values())


def pilot_need_mb(merged_cvars: dict) -> int:
    """Return a pilot job's VRAM need (OQ-28 option (d), the relaxed gate).

    ``VRAM_RESERVE_MB + sum(min(cap, DEFAULT_POOL_MB[c]))``: S6 showed the
    pools in use stay far below their caps, so a cap above the engine
    default does not raise the need.  A pool absent from the CVars counts
    at its default.

    Raises:
        ValueError: as :func:`vram_need_mb`.
    """
    caps = _pool_caps(merged_cvars)
    return VRAM_RESERVE_MB + sum(min(cap, DEFAULT_POOL_MB[name]) for name, cap in caps.items())


def measured_need_mb(measured_peak_mb: int) -> int:
    """Return the VRAM a measured job needs: ``max(ceil(peak * 1.15), peak + 512)``.

    Integer arithmetic (``(peak * 115 + 99) // 100``), so manager and
    worker cannot round differently.

    Raises:
        ValueError: ``measured_peak_mb`` is not an ``int`` (``bool`` is
            not accepted) in ``[1, MAX_PEAK_MB]``.
    """
    if (isinstance(measured_peak_mb, bool) or not isinstance(measured_peak_mb, int)
            or not 1 <= measured_peak_mb <= MAX_PEAK_MB):
        raise ValueError(f"measured peak must be an integer number of MB in [1, {MAX_PEAK_MB}]")
    return max((measured_peak_mb * 115 + 99) // 100, measured_peak_mb + MEASURED_PEAK_FLOOR_MB)


def caps_within_budget(caps_sum: int, total_mb: int) -> bool:
    """The sanity bound: ``caps_sum <= floor(total_mb * VRAM_BUDGET_FRACTION)``."""
    return caps_sum <= math.floor(total_mb * VRAM_BUDGET_FRACTION)


def fits_measured(measured_peak_mb: int, caps_sum: int, total_mb: int, used_mb: int) -> bool:
    """Return whether a measured job fits a GPU (need within usable, caps within budget)."""
    return (measured_need_mb(measured_peak_mb) <= usable_mb(total_mb, used_mb)
            and caps_within_budget(caps_sum, total_mb))
