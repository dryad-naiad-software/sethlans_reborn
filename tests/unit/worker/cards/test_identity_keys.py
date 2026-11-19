# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the chunk-2 parts of ``cards.identity``: the address list of
``SETHLANS_COMPUTE_CARDS`` (``parse_key_list``), saved-key validation
(``is_card_key``), ``has_ids``, and ``revision_id`` in "same model" (D3, C-17).
"""
from types import SimpleNamespace

import pytest

from sethlans_worker_agent.cards.identity import has_ids, is_card_key, parse_key_list, same_model

XT = {'vendor_id': 0x1002, 'device_id': 0x744C, 'name': 'AMD Radeon RX 7900 XT'}


class TestParseKeyList:

    def test_keys_in_the_given_order(self):
        assert parse_key_list('0000:23:00.0,0000:03:00.0', 16) == ('pci:0000:23:00.0', 'pci:0000:03:00.0')

    @pytest.mark.parametrize('text, expected', [
        ('0000:0A:00.0', ('pci:0000:0a:00.0',)),
        ('00000000:01:00.0', ('pci:0000:01:00.0',)),
        ('10000:E1:00.1', ('pci:10000:e1:00.1',)),
        ('apple-gpu', ('apple-gpu',)),
        ('Apple-GPU', ('apple-gpu',)),
        ('  0000:03:00.0  ,\t0000:23:00.0 ', ('pci:0000:03:00.0', 'pci:0000:23:00.0')),
    ])
    def test_normalises_each_entry(self, text, expected):
        assert parse_key_list(text, 16) == expected

    @pytest.mark.parametrize('text', ['pci:0000:03:00.0', 'PCI:0000:03:00.0', 'Pci:0000:03:00.0',
                                      'pCI:0000:03:00.0', ' PCI:00000000:03:00.0 '])
    def test_a_key_copied_from_config_json_is_accepted_in_any_case(self, text):
        assert parse_key_list(text, 16) == ('pci:0000:03:00.0',)

    def test_key_and_address_forms_of_one_card_are_one_key(self):
        assert parse_key_list('pci:0000:0a:00.0,0000:0A:00.0,PCI:0000:0A:00.0', 16) == ('pci:0000:0a:00.0',)

    def test_duplicates_in_any_spelling_are_dropped(self):
        assert parse_key_list('0000:0a:00.0,0000:0A:00.0,00000000:0a:00.0', 16) == ('pci:0000:0a:00.0',)

    @pytest.mark.parametrize('text', ['', '   ', ',', ', ,'])
    def test_empty_list(self, text):
        assert parse_key_list(text, 16) == ()

    @pytest.mark.parametrize('text', [
        'bogus', '0000:03:00.0,bogus', '0000:01:20.0', '0000:01:00.8', '0000:03:00',
        '0000:ffffffff:ffff.ffff', 'apple', 'pci:', 'pci:bogus', 'pci:0000:03:00', 'pci:apple-gpu',
    ])
    def test_any_bad_entry_rejects_the_whole_list(self, text):
        assert parse_key_list(text, 16) is None

    def test_limit_counts_distinct_keys(self):
        three = '0000:01:00.0,0000:02:00.0,0000:03:00.0'
        assert parse_key_list(three, 2) is None
        assert parse_key_list(three, 3) == ('pci:0000:01:00.0', 'pci:0000:02:00.0', 'pci:0000:03:00.0')
        assert parse_key_list('0000:01:00.0,0000:01:00.0,0000:02:00.0', 2) == \
            ('pci:0000:01:00.0', 'pci:0000:02:00.0')


class TestIsCardKey:

    @pytest.mark.parametrize('key', ['apple-gpu', 'pci:0000:03:00.0', 'pci:10000:e1:00.1'])
    def test_normalised_keys(self, key):
        assert is_card_key(key) is True

    @pytest.mark.parametrize('key', ['APPLE-GPU', 'pci:0000:0A:00.0', 'pci:00000000:03:00.0', '0000:03:00.0',
                                     'pci:0000:03:00', 'pci:', '', None, 3])
    def test_anything_else(self, key):
        assert is_card_key(key) is False


class TestHasIds:

    def test_both_ids(self):
        assert has_ids(XT) is True
        assert has_ids(SimpleNamespace(vendor_id=0x10DE, device_id=0)) is True

    @pytest.mark.parametrize('record', [
        {'vendor_id': 0x1002}, {'device_id': 0x744C}, {}, {'vendor_id': True, 'device_id': 1},
        {'vendor_id': '0x1002', 'device_id': 0x744C}, SimpleNamespace(name='x'),
    ])
    def test_missing_or_not_integer(self, record):
        assert has_ids(record) is False


class TestSameModelRevision:
    """C-17: AMD gives several models one device id; the PCI revision tells them apart."""

    def test_same_device_id_with_another_revision_is_another_model(self):
        assert same_model(dict(XT, revision_id=0xC8), dict(XT, revision_id=0xCC)) is False

    def test_same_ids_and_revision_is_the_same_model(self):
        assert same_model(dict(XT, revision_id=0xC8), dict(XT, revision_id=0xC8, name='renamed')) is True

    @pytest.mark.parametrize('saved_rev, now_rev', [(None, 0xC8), (0xC8, None), (None, None), (True, 0xC8)])
    def test_revision_on_one_side_only_is_not_compared(self, saved_rev, now_rev):
        assert same_model(dict(XT, revision_id=saved_rev), dict(XT, revision_id=now_rev)) is True

    def test_revision_zero_is_a_value(self):
        assert same_model(dict(XT, revision_id=0), dict(XT, revision_id=1)) is False

    def test_revision_does_not_rescue_another_device_id(self):
        assert same_model(dict(XT, revision_id=0xC8), dict(XT, device_id=0x7448, revision_id=0xC8)) is False

    def test_without_ids_the_revision_is_ignored_and_names_compared(self):
        saved = {'name': 'AMD Radeon RX 7900 XT', 'revision_id': 0xC8}
        now = {'name': 'amd radeon rx 7900 xt', 'revision_id': 0xCC}
        assert same_model(saved, now) is True

    def test_objects_carry_the_revision_too(self):
        now = SimpleNamespace(vendor_id=0x1002, device_id=0x744C, revision_id=0xCC, name='x')
        assert same_model(dict(XT, revision_id=0xC8), now) is False
