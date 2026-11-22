# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``vram_gate.fits_result`` (Unreal per-machine memory test D6, D7; AC-3): a result peak fits a card when
``peak_mb <= floor(total_mb * 0.93) - used_mb``, with no margin and no floor; with the pilot's baseline as
``used_mb`` it is D6's rule, false exactly when ``peak_mb + baseline_used_mb > floor(total_mb * 0.93)``.
"""
import math

import pytest

from shared.unreal import vram_gate as g


@pytest.mark.parametrize('total, used, line', [
    (32607, 0, 30324), (32607, 1000, 29324), (20464, 500, 18531), (16303, 0, 15161), (24560, 20000, 2840),
])
def test_exactly_at_the_line_fits_and_one_mb_more_does_not(total, used, line):
    assert g.usable_mb(total, used) == line
    assert g.fits_result(line, total, used) is True
    assert g.fits_result(line + 1, total, used) is False


def test_no_margin_and_no_floor():
    """The removed gate needed ``max(ceil(peak * 1.15), peak + 512)``; the result peak alone is compared now."""
    assert g.fits_result(29000, 32607, 1000) is True


@pytest.mark.parametrize('peak, baseline, total', [(29324, 1000, 32607), (13689, 5342, 20464), (1, 0, 2)])
def test_the_pilot_rule_is_peak_plus_baseline_against_the_line(peak, baseline, total):
    line = math.floor(total * g.VRAM_BUDGET_FRACTION)
    assert (peak + baseline > line) is (not g.fits_result(peak, total, baseline))
    assert g.fits_result(line - baseline, total, baseline) is True
    assert g.fits_result(line - baseline + 1, total, baseline) is False


def test_a_card_already_past_its_line_fits_nothing():
    assert g.usable_mb(16303, 16000) < 0
    assert g.fits_result(1, 16303, 16000) is False


def test_the_kept_names():
    assert g.VRAM_BUDGET_FRACTION == 0.93 and g.MIN_PLAUSIBLE_PEAK_MB == 1024 and g.MAX_PEAK_MB == 1_048_576
