# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests: the frozen measurement-key hashes and the CSV switch denylist
(Unreal foundation FR-MU-5, FR-T-4; Dj R3-W9, Sec R3-W7).
"""

import hashlib

import pytest

from shared.unreal import validation as v


class TestOverridesSha256:

    def test_frozen_literal(self):
        expected = hashlib.sha256(b'{"r.Streaming.PoolSize":"3000"}').hexdigest()
        assert v.overrides_sha256({'r.Streaming.PoolSize': '3000'}) == expected
        assert v.overrides_sha256({}) == hashlib.sha256(b'{}').hexdigest() == (
            '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a')

    def test_key_order_does_not_matter(self):
        assert v.overrides_sha256({'a.b': '1', 'c.d': '2'}) == v.overrides_sha256({'c.d': '2', 'a.b': '1'})

    def test_values_are_strings(self):
        assert v.overrides_sha256({'a.b': 4}) == v.overrides_sha256({'a.b': '4'})

    @pytest.mark.parametrize('value', [None, [], 'x', 5, ['a.b', 1]])
    def test_non_dict_hashes_as_empty(self, value):
        assert v.overrides_sha256(value) == v.overrides_sha256({})

    @pytest.mark.parametrize('value', [{1: object()}, {'x': None}, {'é': 'ß'}, {(1, 2): [3]}])
    def test_never_raises(self, value):
        digest = v.overrides_sha256(value)
        assert len(digest) == 64 and digest == digest.lower()

    def test_values_change_the_hash(self):
        assert v.overrides_sha256({'a.b': '1'}) != v.overrides_sha256({'a.b': '2'})


class TestTargetSha256:

    def test_frozen_literal(self):
        expected = hashlib.sha256(b'["5.8.4-58902052","/Game/M.M","/Game/S.S","/Game/P.P"]').hexdigest()
        assert v.target_sha256('5.8.4-58902052', '/Game/M.M', '/Game/S.S', '/Game/P.P') == expected

    def test_none_is_empty(self):
        assert v.target_sha256(None, None, None, None) == v.target_sha256('', '', '', '')

    def test_each_part_matters(self):
        base = ('5.8.4-1', '/Game/M.M', '/Game/S.S', '/Game/P.P')
        digests = {v.target_sha256(*base)}
        for index in range(4):
            changed = list(base)
            changed[index] = changed[index] + 'x'
            digests.add(v.target_sha256(*changed))
        assert len(digests) == 5

    def test_never_raises(self):
        assert len(v.target_sha256(object(), 1, 2.5, b'x')) == 64


@pytest.mark.parametrize('name', ['a.csvexeccmds', 'r.x_csvfile', 'a.csvcaptureframes', 'a.b_csvgpustats',
                                  'a.csvfilename', 'a.csvmetadata', 'x.csvcategories'])
def test_csv_switches_are_denied(name):
    with pytest.raises(ValueError):
        v.validate_cvars({name: '1'})


def test_csv_switches_are_listed():
    assert {'csvcaptureframes', 'csvgpustats', 'csvexeccmds', 'csvfile', 'csvfilename',
            'csvmetadata', 'csvcategories'} <= v.UE_SWITCH_DENYLIST
