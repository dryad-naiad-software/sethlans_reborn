# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the manager's card brand rule (Unreal render per card D7,
FR-PC-BR-1, FR-PC-BR-2, FR-PC-BR-5): the brand from the vendor id or the
Blender backend, unknown brands, and when ticked cards may go together
(one known brand; a card of unknown brand only on its own).
"""

import pytest

from workers.services import card_brand
from workers.services.card_brand import MIXED_BRANDS_TEXT, brand, mixed_brands


class TestBrand:

    @pytest.mark.parametrize('vendor_id, name', [(0x10DE, 'NVIDIA'), (0x1002, 'AMD'), (0x8086, 'Intel')])
    def test_known_vendor_ids(self, vendor_id, name):
        assert brand(vendor_id, None) == name

    @pytest.mark.parametrize('vendor_id, name', [(0x1AF4, 'PCI 1af4'), (0x0001, 'PCI 0001'), (0xFFFF, 'PCI ffff')])
    def test_other_vendor_id_is_its_pci_id(self, vendor_id, name):
        assert brand(vendor_id, 'HIP') == name

    def test_vendor_id_wins_over_the_backend(self):
        assert brand(0x10DE, 'HIP') == 'NVIDIA'

    @pytest.mark.parametrize('backend, name', [
        ('CUDA', 'NVIDIA'), ('OPTIX', 'NVIDIA'), ('HIP', 'AMD'), ('ONEAPI', 'Intel'), ('METAL', 'Apple'),
    ])
    def test_backend_family_without_a_vendor_id(self, backend, name):
        assert brand(None, backend) == name

    @pytest.mark.parametrize('vendor_id, backend', [
        (None, None), (None, 'VULKAN'), (None, 'cuda'), (None, 3),
        (True, None), (-1, None), (0x10000, None), ('4318', None), (4318.0, None),
    ])
    def test_unknown(self, vendor_id, backend):
        assert brand(vendor_id, backend) is None

    def test_the_brand_names_are_the_only_vendor_names(self):
        """NF-5: D7's names, nothing else."""
        assert set(card_brand.VENDOR_BRANDS.values()) | set(card_brand.BACKEND_BRANDS.values()) == {
            'NVIDIA', 'AMD', 'Intel', 'Apple'}


class TestMixedBrands:

    @pytest.mark.parametrize('brands', [
        [], ['NVIDIA'], ['NVIDIA', 'NVIDIA'], [None], ['PCI 1af4', 'PCI 1af4'],
    ])
    def test_allowed(self, brands):
        assert mixed_brands(brands) is None

    @pytest.mark.parametrize('brands, names', [
        (['NVIDIA', 'AMD'], 'AMD, NVIDIA'),
        (['NVIDIA', 'AMD', 'NVIDIA'], 'AMD, NVIDIA'),
        (['Intel', 'AMD', None], 'AMD, Intel, unknown'),
        ([None, None], 'unknown'),
        (['AMD', None], 'AMD, unknown'),
        ([None, 'NVIDIA', 'NVIDIA'], 'NVIDIA, unknown'),
        (['NVIDIA', None, None], 'NVIDIA, unknown'),
        (['PCI 1af4', 'PCI 15ad'], 'PCI 15ad, PCI 1af4'),
    ])
    def test_refused(self, brands, names):
        assert mixed_brands(brands) == MIXED_BRANDS_TEXT.format(brands=names)

    def test_the_sentence(self):
        assert mixed_brands(['AMD', 'NVIDIA']) == (
            "Graphics cards of more than one brand (AMD, NVIDIA) cannot be ticked together on one machine; "
            "this is not supported. Tick the cards of one brand only.")

    def test_accepts_a_generator(self):
        assert mixed_brands(name for name in ('AMD', 'NVIDIA')) is not None
