# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests: ``shared.unreal.vram_gate`` after the per-machine memory test's D7
removals (the pilot need, the pool-caps bound, the reserve and the
measured-need gate are gone).
"""

import pytest

from shared.unreal import vram_gate as v


def test_constants():
    assert v.MIN_PLAUSIBLE_PEAK_MB == 1024


@pytest.mark.parametrize('name', ['fits_measured', 'pilot_need_mb', 'caps_within_budget', 'caps_sum_mb',
                                  'MEASURED_PEAK_MARGIN', 'measured_need_mb', 'vram_need_mb', 'VRAM_RESERVE_MB',
                                  'DEFAULT_POOL_MB', 'MEASURED_PEAK_FLOOR_MB'])
def test_removed_names_are_gone(name):
    """D7: no pilot need, no pool-caps bound, no reserve and no margin."""
    assert not hasattr(v, name)
