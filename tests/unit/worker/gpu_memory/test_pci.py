# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``gpu_memory.pci``: one text form for every PCI address the
worker meets, and ``None`` for anything out of range.
"""
import pytest

from sethlans_worker_agent.gpu_memory.pci import format_pci, normalize_pci


class TestNormalizePciText:

    @pytest.mark.parametrize('text, expected', [
        ('0000:01:00.0', '0000:01:00.0'),             # Linux sysfs
        ('0000:23:00.0', '0000:23:00.0'),
        ('0000:79:00.0', '0000:79:00.0'),
        ('00000000:01:00.0', '0000:01:00.0'),         # nvidia-smi, 8-digit domain
        ('0000:0A:1F.7', '0000:0a:1f.7'),             # upper case is lowered; device/function at their maximum
        ('10000:e1:00.0', '10000:e1:00.0'),           # Linux VMD domain keeps its five digits
        ('ffffffff:ff:1f.7', 'ffffffff:ff:1f.7'),     # every part at its maximum
    ])
    def test_valid_text_is_normalised(self, text, expected):
        assert normalize_pci(text) == expected

    @pytest.mark.parametrize('text', [
        '0000:ffffffff:ffff.ffff',   # the software adapter's all-ones address
        '0000:01:20.0',              # device above 0x1f
        '0000:01:00.8',              # function above 7
        '0000:01:00.f',              # function in hex
        '0000:01:00',                # no function
        '000:01:00.0',               # domain too short
        '000000000:01:00.0',         # domain too long (9 digits)
        '0000:1:00.0',               # bus with one digit
        '0000:100:00.0',             # bus above 0xff
        ' 0000:01:00.0',             # surrounding whitespace is not stripped
        '0000:01:00.0\n',
        'pci:0000:01:00.0',          # a card key is not an address
        'gggg:01:00.0',
        '',
        'garbage',
    ])
    def test_invalid_text_is_none(self, text):
        assert normalize_pci(text) is None


class TestNormalizePciTriple:

    @pytest.mark.parametrize('triple, expected', [
        ((1, 0, 0), '0000:01:00.0'),
        ((0x23, 0, 0), '0000:23:00.0'),
        ((0xff, 0x1f, 7), '0000:ff:1f.7'),
        ((0, 0, 0), '0000:00:00.0'),
    ])
    def test_d3dkmt_triple_gets_domain_zero(self, triple, expected):
        assert normalize_pci(triple) == expected

    @pytest.mark.parametrize('triple', [
        (0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF),   # what the software adapter reports
        (0x100, 0, 0),
        (0, 0x20, 0),
        (0, 0, 8),
        (-1, 0, 0),
        (1, 0),
        (1, 0, 0, 0),
        (True, 0, 0),                           # bool is not an address part
        ('1', 0, 0),
        (1.0, 0, 0),
    ])
    def test_out_of_range_or_malformed_triple_is_none(self, triple):
        assert normalize_pci(triple) is None

    @pytest.mark.parametrize('value', [None, 1, b'0000:01:00.0', ['0000:01:00.0'], [1, 0, 0]])
    def test_other_types_are_none(self, value):
        assert normalize_pci(value) is None


class TestFormatPci:

    def test_formats_lower_case_with_padding(self):
        assert format_pci(0, 0xA, 0x1, 0x2) == '0000:0a:01.2'

    def test_domain_above_four_digits_keeps_its_digits(self):
        assert format_pci(0x10000, 1, 0, 0) == '10000:01:00.0'

    @pytest.mark.parametrize('parts', [
        (0x1_0000_0000, 0, 0, 0),
        (0, 0x100, 0, 0),
        (0, 0, 0x20, 0),
        (0, 0, 0, 8),
        (0, False, 0, 0),
        (None, 0, 0, 0),
    ])
    def test_out_of_range_parts_are_none(self, parts):
        assert format_pci(*parts) is None
