# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``shared.unreal.vram_gate`` (Unreal foundation D2, FR-MU-5, FR-UE-21, FR-T-4).
"""
from shared.unreal import vram_gate as g

POOL = 'r.Streaming.PoolSize'
RT = 'r.RayTracing.ResidentGeometryMemoryPoolSizeInMB'


def test_s6_constants():
    assert g.VRAM_BUDGET_FRACTION == 0.93
    assert g.POOL_CVARS == (POOL, RT)
    assert not hasattr(g, 'POOL_VRAM_FRACTION')


def test_usable_mb_floors():
    assert g.usable_mb(32607, 1000) == 29324
    assert g.usable_mb(16303, 1000) == 14161
    assert g.usable_mb(12282, 1000) == 10422
