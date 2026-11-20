# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The Unreal E2E's upper bound for a pilot peak (``peak_upper_bound_mb``).

The bound is the measured card's own memory, taken from the worker's
heartbeat reading, so a plausible peak passes on any brand of card.
"""

import pytest

from tests.e2e.unreal_e2e_config import MIN_PILOT_PEAK_MB, peak_upper_bound_mb

AMD = {"card_key": "pci:0000:03:00.0", "name": "AMD Radeon RX 7900 XT", "total_mb": 20420}
NVIDIA = {"card_key": "pci:0000:01:00.0", "name": "NVIDIA GeForce RTX 5090", "total_mb": 32607}
IGPU = {"card_key": "pci:0000:7c:00.0", "name": "AMD Radeon(TM) Graphics", "total_mb": 512}


@pytest.mark.parametrize("card, peak", [(AMD, 13689), (NVIDIA, 9229)])
def test_measured_peak_is_within_its_card(card, peak):
    bound = peak_upper_bound_mb([IGPU, card], card["name"], None)
    assert bound == card["total_mb"]
    assert MIN_PILOT_PEAK_MB <= peak <= bound


def test_peak_above_the_card_total_is_out_of_bounds():
    assert 20421 > peak_upper_bound_mb([AMD], AMD["name"], None)


def test_two_identical_cards_give_their_total():
    twin = {**AMD, "card_key": "pci:0000:04:00.0"}
    assert peak_upper_bound_mb([AMD, twin], AMD["name"], None) == 20420


@pytest.mark.parametrize("gpus", [None, [], [IGPU], [{"name": AMD["name"]}]])
def test_no_matching_reading_uses_the_measurement_total(gpus):
    assert peak_upper_bound_mb(gpus, AMD["name"], 20464) == 20464


def test_same_name_different_totals_uses_the_measurement_total():
    other = {**AMD, "card_key": "pci:0000:04:00.0", "total_mb": 16368}
    assert peak_upper_bound_mb([AMD, other], AMD["name"], 20464) == 20464


def test_unknown_everywhere_is_none():
    assert peak_upper_bound_mb([IGPU], AMD["name"], None) is None
