# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``shared.unreal.vram_gate`` (Unreal foundation D2, FR-MU-5, FR-UE-21, FR-T-4, AC-30).
"""
import pytest

from shared.unreal import vram_gate as g

POOL = 'r.Streaming.PoolSize'
RT = 'r.RayTracing.ResidentGeometryMemoryPoolSizeInMB'


def _caps(pool, rt):
    return {POOL: str(pool), RT: str(rt), 'gc.TimeBetweenPurgingPendingKillObjects': '4'}


def test_s6_constants():
    assert g.VRAM_RESERVE_MB == 9078
    assert g.VRAM_BUDGET_FRACTION == 0.93
    assert g.DEFAULT_POOL_MB == {POOL: 3000, RT: 400}
    assert g.POOL_CVARS == (POOL, RT)
    assert not hasattr(g, 'POOL_VRAM_FRACTION')


def test_need_sums_the_caps():
    assert g.vram_need_mb(_caps(9000, 2048)) == 20126
    assert g.vram_need_mb(_caps(3000, 2048)) == 14126


def test_absent_pools_count_at_their_defaults():
    assert g.vram_need_mb({}) == 12478
    assert g.vram_need_mb({POOL: '1000'}) == 9078 + 1000 + 400


def test_names_are_case_insensitive():
    assert g.vram_need_mb({'r.streaming.poolsize': 9000}) == 9078 + 9000 + 400
    with pytest.raises(ValueError):
        g.vram_need_mb({POOL: '1', 'R.STREAMING.POOLSIZE': '2'})


@pytest.mark.parametrize('value', ['1.5', 'x', True, '-1', ''])
def test_bad_pool_values_raise(value):
    with pytest.raises(ValueError):
        g.vram_need_mb({POOL: value})


def test_usable_mb_floors():
    assert g.usable_mb(32607, 1000) == 29324
    assert g.usable_mb(16303, 1000) == 14161
    assert g.usable_mb(12282, 1000) == 10422


@pytest.mark.parametrize('caps, total, used, expected', [
    ((9000, 2048), 32607, 1000, True),
    ((9000, 2048), 20464, 1000, False),
    ((9000, 2048), 16303, 1000, False),
    ((2048, 1024), 16303, 1000, True),
    ((2048, 1024), 12282, 1000, False),
    ((3000, 2048), 16303, 1000, True),
    ((3000, 2048), 16303, 2000, False),
    ((1000, 1024), 16303, 1000, True),
])
def test_ac30_table(caps, total, used, expected):
    assert g.fits(g.vram_need_mb(_caps(*caps)), total, used) is expected


def test_no_pool_cvar_is_still_gated_by_the_reserve():
    need = g.vram_need_mb({})
    assert g.fits(need, 16303, 1000) is True
    assert g.fits(need, 12282, 1000) is False


def test_pools_are_summed_not_compared_one_by_one():
    """Each pool passes a 0.6 x VRAM check on its own; the sum does not fit."""
    total = 16303
    pool, rt = 6000, 5000
    assert pool <= 0.6 * total and rt <= 0.6 * total
    assert g.fits(g.vram_need_mb(_caps(pool, rt)), total, 0) is False
