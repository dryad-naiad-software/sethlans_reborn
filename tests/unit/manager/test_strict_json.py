# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests: ``workers.strict_json`` (Unreal foundation FR-PM-4, FR-PM-6).
"""

import math

import pytest

from workers import strict_json as s
from workers.views import heartbeat_unreal


def test_heartbeat_uses_the_shared_helpers():
    assert heartbeat_unreal._is_int is s._is_int
    assert heartbeat_unreal._int_in is s._int_in


@pytest.mark.parametrize('value, expected', [
    (1, True), (0, True), (-5, True), (True, False), (False, False), (1.0, False), ('1', False), (None, False),
])
def test_is_int(value, expected):
    assert s._is_int(value) is expected


@pytest.mark.parametrize('value, expected', [
    (1, True), (1.5, True), (True, False), ('1.5', False), (math.nan, False), (math.inf, False),
    (-math.inf, False), (None, False),
])
def test_is_number(value, expected):
    assert s.is_number(value) is expected


def test_ranges():
    assert s._int_in(5, 0, 5) and not s._int_in(6, 0, 5)
    assert s.number_in(0.5, 0, 1) and not s.number_in(1.5, 0, 1) and not s.number_in(True, 0, 1)


class TestUnknownKeys:

    def test_every_level(self):
        data = {'a': 1, 'zz': 2, 'gpu': {'index': 0, 'vendor': 'x'},
                'shots': [{'frame': 1}, {'frame': 2, 'y': 3}, 'not-a-dict']}
        assert s.unknown_keys(data, {'a', 'gpu', 'shots'}, nested={'gpu': {'index'}},
                              lists={'shots': {'frame'}}) == ['gpu.vendor', 'shots[1].y', 'zz']

    @pytest.mark.parametrize('data', [None, [], 'x', 5])
    def test_non_dict(self, data):
        assert s.unknown_keys(data, set()) == []

    def test_nested_value_of_the_wrong_type_is_left_to_the_serializer(self):
        assert s.unknown_keys({'gpu': 5, 'shots': 'x'}, {'gpu', 'shots'}, nested={'gpu': set()},
                              lists={'shots': set()}) == []
