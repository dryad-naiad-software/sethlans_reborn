# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``shared.unreal.card_kind`` (Unreal per-machine memory test D1, FR-MT-K-1).

- ``card_kind``: ``vvvv:dddd:rrrr`` in four lower-case hex digits each;
  ``None`` when the vendor or device id is missing or not an integer PCI id
  (``0..0xFFFF``, a ``bool`` is not one); a missing or invalid revision
  gives ``none``.
- ``is_card_kind`` / ``KIND_RE``: the exact form, anchored with ``\\Z`` so a
  trailing newline is refused.
"""
import re

import pytest

from shared.unreal import card_kind as ck

AMD, NVIDIA = 0x1002, 0x10DE

INVALID_IDS = [None, True, False, -1, 0x10000, 1.0, '1002', b'\x10\x02', [0x1002]]
INVALID_ID_NAMES = ['none', 'true', 'false', 'negative', 'over-ffff', 'float', 'str', 'bytes', 'list']


class TestConstants:

    def test_values(self):
        assert ck.MAX_PCI_ID == 0xFFFF
        assert ck.NO_REVISION == 'none'
        assert ck.KIND_RE == r'^[0-9a-f]{4}:[0-9a-f]{4}:([0-9a-f]{4}|none)\Z'

    def test_kind_re_ends_in_backslash_z_not_dollar(self):
        assert ck.KIND_RE.endswith(r'\Z')
        assert not ck.KIND_RE.endswith('$')


class TestCardKind:

    def test_amd_card_with_revision(self):
        assert ck.card_kind(AMD, 0x744C, 0xCC) == '1002:744c:00cc'

    def test_nvidia_card_with_revision(self):
        assert ck.card_kind(NVIDIA, 0x2704, 0xA1) == '10de:2704:00a1'

    def test_lower_case_hex_and_four_digits_each(self):
        kind = ck.card_kind(0xABCD, 0xEF01, 0xFF)
        assert kind == 'abcd:ef01:00ff'
        assert kind == kind.lower()
        assert [len(part) for part in kind.split(':')] == [4, 4, 4]

    def test_zero_ids_are_padded_not_missing(self):
        assert ck.card_kind(0, 0, 0) == '0000:0000:0000'

    def test_maximum_ids(self):
        assert ck.card_kind(0xFFFF, 0xFFFF, 0xFFFF) == 'ffff:ffff:ffff'

    def test_revision_above_one_byte_is_kept(self):
        assert ck.card_kind(AMD, 0x744C, 0x1234) == '1002:744c:1234'

    def test_missing_revision_is_none(self):
        assert ck.card_kind(AMD, 0x744C, None) == '1002:744c:none'

    @pytest.mark.parametrize('revision', INVALID_IDS, ids=INVALID_ID_NAMES)
    def test_invalid_revision_is_none(self, revision):
        assert ck.card_kind(AMD, 0x744C, revision) == '1002:744c:none'

    @pytest.mark.parametrize('vendor', INVALID_IDS, ids=INVALID_ID_NAMES)
    def test_missing_or_invalid_vendor_gives_no_kind(self, vendor):
        assert ck.card_kind(vendor, 0x744C, 0xCC) is None

    @pytest.mark.parametrize('device', INVALID_IDS, ids=INVALID_ID_NAMES)
    def test_missing_or_invalid_device_gives_no_kind(self, device):
        assert ck.card_kind(AMD, device, 0xCC) is None

    def test_no_ids_at_all_gives_no_kind(self):
        assert ck.card_kind(None, None, None) is None

    def test_every_produced_kind_is_a_card_kind(self):
        for args in ((AMD, 0x744C, 0xCC), (AMD, 0x744C, None), (0, 0, 0), (0xFFFF, 0xFFFF, 0xFFFF),
                     (NVIDIA, 0x2704, True)):
            assert ck.is_card_kind(ck.card_kind(*args)), args


class TestIsCardKind:

    @pytest.mark.parametrize('value', ['1002:744c:00cc', '10de:2704:none', '0000:0000:0000', 'ffff:ffff:ffff'])
    def test_accepts_the_form(self, value):
        assert ck.is_card_kind(value) is True

    @pytest.mark.parametrize('value', [
        '1002:744c:00cc\n', '1002:744c:none\n', '1002:744C:00cc', '1002:744c:NONE', 'ABCD:744c:00cc',
        '100:744c:00cc', '1002:74c:00cc', '1002:744c:0cc', '10002:744c:00cc', '1002:744c:000cc',
        '1002:744c', '1002:744c:', '1002-744c-00cc', ' 1002:744c:00cc', '1002:744c:00cc ',
        '1002:744c:00cg', '1002:744c:nan', '', '\n',
        '１002:744c:00cc',  # a full-width digit: not ASCII hex
    ], ids=[
        'trailing-newline', 'none-trailing-newline', 'upper-device', 'upper-none', 'upper-vendor',
        'three-digit-vendor', 'three-digit-device', 'three-digit-revision', 'five-digit-vendor',
        'five-digit-revision', 'two-parts', 'empty-revision', 'dashes', 'leading-space', 'trailing-space',
        'non-hex', 'other-word', 'empty', 'newline-only', 'full-width-digit',
    ])
    def test_rejects_other_strings(self, value):
        assert ck.is_card_kind(value) is False

    @pytest.mark.parametrize('value', [None, 0x1002, True, b'1002:744c:00cc', ['1002:744c:00cc']],
                             ids=['none', 'int', 'bool', 'bytes', 'list'])
    def test_rejects_non_strings(self, value):
        assert ck.is_card_kind(value) is False


class TestKindRe:

    def test_dollar_would_have_accepted_a_trailing_newline(self):
        """The reason for ``\\Z``: ``$`` matches just before a final newline."""
        dollar = ck.KIND_RE[:-2] + '$'
        assert re.match(dollar, '1002:744c:00cc\n') is not None
        assert re.match(ck.KIND_RE, '1002:744c:00cc\n') is None

    def test_pattern_compiles_and_matches_a_kind(self):
        assert re.fullmatch(ck.KIND_RE, '1002:744c:none') is not None
