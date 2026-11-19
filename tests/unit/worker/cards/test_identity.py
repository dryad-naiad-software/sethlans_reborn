# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``cards.identity`` (FR-CI-1, D1, D3, D4): card keys, the
address in a Blender device id, key order and "same model".
"""
from types import SimpleNamespace

import pytest

from sethlans_worker_agent.cards.identity import (
    APPLE_GPU_KEY, blender_pci, card_key, clean_name, key_address, key_sort_value, normalize_name, same_model,
)
from tests.unit.worker.cards._card_ids import (
    RADEON_IGPU, RTX_5090_CUDA, RTX_5090_OPTIX, RX_7900_XT_03, RX_7900_XT_23,
)


class TestCardKey:

    @pytest.mark.parametrize('text, expected', [
        ('0000:01:00.0', 'pci:0000:01:00.0'),
        ('0000:23:00.0', 'pci:0000:23:00.0'),
        ('0000:0A:00.0', 'pci:0000:0a:00.0'),
        ('00000000:01:00.0', 'pci:0000:01:00.0'),
        ('10000:01:00.0', 'pci:10000:01:00.0'),
    ])
    def test_key_is_pci_prefix_and_normalised_address(self, text, expected):
        assert card_key(text) == expected

    @pytest.mark.parametrize('text', [
        '0000:ffffffff:ffff.ffff', '0000:01:20.0', '0000:01:00.8', '0000:01:00', 'NVIDIA GeForce RTX 5090', '',
    ])
    def test_unnormalisable_text_has_no_key(self, text):
        assert card_key(text) is None

    @pytest.mark.parametrize('value', [None, (1, 0, 0), 1])
    def test_non_text_has_no_key(self, value):
        assert card_key(value) is None


class TestBlenderPci:

    @pytest.mark.parametrize('device_id, expected', [
        (RTX_5090_OPTIX, (0, 1, 0)),
        (RTX_5090_CUDA, (0, 1, 0)),
        (RADEON_IGPU, (0, 0x79, 0)),
        (RX_7900_XT_03, (0, 3, 0)),
        (RX_7900_XT_23, (0, 0x23, 0)),
    ])
    def test_realistic_ids(self, device_id, expected):
        assert blender_pci(device_id) == expected

    @pytest.mark.parametrize('suffix', ['.0', '.1', '.7'])
    def test_trailing_function_is_accepted_and_dropped(self, suffix):
        assert blender_pci(f'HIP_AMD Radeon RX 7900 XT_0000:23:00{suffix}') == (0, 0x23, 0)

    def test_function_out_of_range_is_rejected(self):
        assert blender_pci('HIP_AMD Radeon RX 7900 XT_0000:23:00.8') is None

    def test_five_digit_domain_is_kept_as_an_integer(self):
        assert blender_pci('ONEAPI_Intel Arc A770_10000:01:00') == (0x10000, 1, 0)

    def test_eight_digit_domain_equals_four_digit_domain(self):
        assert blender_pci('CUDA_NVIDIA GeForce RTX 5090_00000000:01:00') == blender_pci(RTX_5090_CUDA)

    def test_upper_case_hex_is_accepted(self):
        assert blender_pci('HIP_AMD Radeon_0000:0A:1F') == (0, 0x0a, 0x1f)

    def test_optix_suffix_is_dropped_before_the_last_underscore(self):
        assert blender_pci('OPTIX_NVIDIA_0000:02:00_OptiX') == (0, 2, 0)

    @pytest.mark.parametrize('device_id', [
        'NVIDIA GeForce RTX 5090',                      # a bare name
        'CUDA_NVIDIA GeForce RTX 5090',                 # missing address
        'CUDA_NVIDIA GeForce RTX 5090_OptiX',           # missing address, OptiX form
        'CUDA_NVIDIA GeForce RTX 5090_0000:01:00_',     # empty last part
        'CUDA_NVIDIA GeForce RTX 5090_0000:01:00_optix',  # the suffix is case-sensitive
        'CUDA_NVIDIA_0000:01',                          # no device part
        'CUDA_NVIDIA_000:01:00',                        # domain too short
        'CUDA_NVIDIA_000000000:01:00',                  # domain too long
        'CUDA_NVIDIA_0000:001:00',                      # bus too long
        'CUDA_NVIDIA_0000:01:00.0.0',
        'METAL_Apple M2 Max',                           # Metal ids carry no address
        '',
        '____',
        '\x00\xff garbage',
    ])
    def test_bad_forms_are_none(self, device_id):
        assert blender_pci(device_id) is None

    @pytest.mark.parametrize('value', [None, 1, b'CUDA_X_0000:01:00', ('0000:01:00',)])
    def test_non_text_is_none(self, value):
        assert blender_pci(value) is None


class TestKeyAddress:

    def test_parses_a_card_key(self):
        assert key_address('pci:0000:23:00.0') == (0, 0x23, 0, 0)

    def test_parses_a_five_digit_domain(self):
        assert key_address('pci:10000:e1:00.1') == (0x10000, 0xe1, 0, 1)

    @pytest.mark.parametrize('key', [APPLE_GPU_KEY, '0000:23:00.0', 'pci:0000:23:00', None, 5])
    def test_anything_else_has_no_address(self, key):
        assert key_address(key) is None


class TestKeySortValue:

    def test_pci_address_order_with_integer_domains(self):
        keys = ['pci:10000:00:00.0', 'pci:0000:79:00.0', APPLE_GPU_KEY, 'pci:0000:23:00.0',
                'pci:0000:03:00.0', 'pci:0000:03:00.1', 'pci:0001:00:00.0']

        ordered = sorted(keys, key=key_sort_value)

        assert ordered == ['pci:0000:03:00.0', 'pci:0000:03:00.1', 'pci:0000:23:00.0', 'pci:0000:79:00.0',
                           'pci:0001:00:00.0', 'pci:10000:00:00.0', APPLE_GPU_KEY]

    def test_key_without_address_sorts_last(self):
        assert key_sort_value(None) == (1,)
        assert key_sort_value('pci:0000:01:00.0') == (0, 0, 1, 0, 0)


class TestSameModel:

    def test_equal_ids_are_the_same_model_whatever_the_names(self):
        saved = {'vendor_id': 0x1002, 'device_id': 0x744C, 'name': 'AMD Radeon RX 7900 XT'}
        now = {'vendor_id': 0x1002, 'device_id': 0x744C, 'name': 'Renamed by a driver update'}
        assert same_model(saved, now) is True

    def test_different_device_id_is_another_model_even_with_the_same_name(self):
        saved = {'vendor_id': 0x1002, 'device_id': 0x744C, 'name': 'AMD Radeon'}
        now = {'vendor_id': 0x1002, 'device_id': 0x7448, 'name': 'AMD Radeon'}
        assert same_model(saved, now) is False

    def test_different_vendor_is_another_model(self):
        saved = {'vendor_id': 0x10DE, 'device_id': 0x2B85, 'name': 'x'}
        now = {'vendor_id': 0x1002, 'device_id': 0x2B85, 'name': 'x'}
        assert same_model(saved, now) is False

    def test_missing_ids_on_one_side_compare_normalised_names(self):
        saved = {'vendor_id': 0x10DE, 'device_id': 0x2B85, 'name': 'NVIDIA  GeForce RTX 5090'}
        now = {'name': 'nvidia geforce\trtx 5090'}
        assert same_model(saved, now) is True

    def test_missing_ids_and_different_names_are_another_model(self):
        assert same_model({'name': 'AMD Radeon RX 7900 XT'}, {'name': 'AMD Radeon RX 7900 XTX'}) is False

    def test_bool_ids_count_as_missing(self):
        saved = {'vendor_id': True, 'device_id': 1, 'name': 'A'}
        now = {'vendor_id': True, 'device_id': 1, 'name': 'B'}
        assert same_model(saved, now) is False

    def test_objects_and_dicts_mix(self):
        saved = SimpleNamespace(vendor_id=0x1002, device_id=0x744C, name='a')
        now = {'vendor_id': 0x1002, 'device_id': 0x744C, 'name': 'b'}
        assert same_model(saved, now) is True

    def test_two_records_without_names_are_equal(self):
        assert same_model({}, SimpleNamespace()) is True


class TestNames:

    def test_normalize_name_folds_case_and_collapses_spaces(self):
        assert normalize_name('  AMD\tRadeon   RX 7900 XT \n') == 'amd radeon rx 7900 xt'

    def test_normalize_name_of_none_is_empty(self):
        assert normalize_name(None) == ''

    def test_clean_name_drops_control_characters(self):
        assert clean_name('AMD\x00Radeon\x1b[31m RX\r\n7900') == 'AMD Radeon [31m RX 7900'

    def test_clean_name_keeps_non_ascii(self):
        assert clean_name('Radeon™ Graphics') == 'Radeon™ Graphics'

    def test_clean_name_is_capped(self):
        assert clean_name('x' * 500) == 'x' * 128

    @pytest.mark.parametrize('value', [None, 5, b'AMD'])
    def test_clean_name_of_non_text_is_empty(self, value):
        assert clean_name(value) == ''
