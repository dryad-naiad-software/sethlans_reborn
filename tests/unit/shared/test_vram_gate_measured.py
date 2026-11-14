# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests: the measured-peak and pilot additions to ``shared.unreal.vram_gate``
(Unreal foundation D2, FR-MU-5, FR-T-4, AC-30; OQ-28 option (d)).
"""

import pytest

from shared.unreal import vram_gate as v

POC_CAPS = {'r.Streaming.PoolSize': '9000', 'r.RayTracing.ResidentGeometryMemoryPoolSizeInMB': '2048'}


def test_constants():
    assert v.MEASURED_PEAK_MARGIN == 1.15
    assert v.MEASURED_PEAK_FLOOR_MB == 512
    assert v.MIN_PLAUSIBLE_PEAK_MB == 1024
    assert v.VRAM_RESERVE_MB == 9078


@pytest.mark.parametrize('peak, need', [
    (7930, 9120), (100, 612), (1024, 1536), (3413, 3925), (3414, 3927), (1, 513), (1_048_576, 1_205_863),
])
def test_measured_need(peak, need):
    assert v.measured_need_mb(peak) == need


@pytest.mark.parametrize('bad', [True, False, 0, -1, 1_048_577, 7930.0, '7930', None])
def test_measured_need_rejects(bad):
    with pytest.raises(ValueError):
        v.measured_need_mb(bad)


def test_caps_sum():
    assert v.caps_sum_mb(POC_CAPS) == 11048
    assert v.caps_sum_mb({}) == 3400
    with pytest.raises(ValueError):
        v.caps_sum_mb({'r.Streaming.PoolSize': 'x'})


class TestPilotNeed:
    """OQ-28 option (d): reserve + sum(min(cap, default)), absent pools at the default."""

    def test_poc_caps_are_capped_at_the_defaults(self):
        assert v.pilot_need_mb(POC_CAPS) == 9078 + 3000 + 400 == 12478

    def test_no_pools_is_the_reserve_plus_defaults(self):
        assert v.pilot_need_mb({}) == 12478

    def test_smaller_caps_count_as_set(self):
        caps = {'r.Streaming.PoolSize': '1000', 'r.RayTracing.ResidentGeometryMemoryPoolSizeInMB': '100'}
        assert v.pilot_need_mb(caps) == 9078 + 1000 + 100

    def test_sixteen_gb_card_fits(self):
        assert v.usable_mb(16303, 0) == 15161
        assert v.fits(v.pilot_need_mb(POC_CAPS), 16303, 0)

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            v.pilot_need_mb({'r.Streaming.PoolSize': True})


def test_caps_within_budget():
    assert v.caps_within_budget(11048, 12282)       # floor(12282 * 0.93) = 11422
    assert not v.caps_within_budget(11048, 11264)   # 10475


@pytest.mark.parametrize('total, eligible', [
    (32607, True), (16303, True), (12282, True), (11264, False), (10240, False),
])
def test_fits_measured_worked_example(total, eligible):
    """AC-30: peak 7,930 (need 9,120), caps 9,000 + 2,048, 1,000 MB in use."""
    assert v.fits_measured(7930, 11048, total, 1000) is eligible


def test_sanity_bound_alone_skips():
    assert v.measured_need_mb(7930) <= v.usable_mb(11264, 0)
    assert not v.fits_measured(7930, 11048, 11264, 0)
