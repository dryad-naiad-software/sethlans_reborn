# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The brand of a card and the one-brand rule of ``cards.store.save`` (unreal-render-per-card D7, FR-PC-BR-1/2).

A machine may have cards of two brands; only ticking both is refused. A card of unknown brand may be ticked
only on its own. The rule holds whatever the compute method (Unreal uses the ticked cards with ``cpu`` too).
"""
import pytest

from sethlans_worker_agent.cards import brand as brand_module
from sethlans_worker_agent.cards import store
from sethlans_worker_agent.cards.brand import MIXED_BRANDS_TEXT, brand, mixed_brands
from tests.unit.worker._card_choice import KEY_IGPU, KEY_RTX_A, KEY_RTX_B, KEY_XT_03, card, igpu, inv, rtx_a, rtx_b, xt_03
from tests.unit.worker._card_pages import fake_saved

KEY_ODD = 'pci:0000:41:00.0'
KEY_ODD_2 = 'pci:0000:42:00.0'
MIXED_AMD_NVIDIA = ("Graphics cards of more than one brand (AMD, NVIDIA) cannot be ticked together on one machine; "
                    "this is not supported. Tick the cards of one brand only.")


def _unknown(key=KEY_ODD):
    """A card with no vendor id and no Blender match: its brand is unknown."""
    return card(key, 'Unknown card')


@pytest.fixture
def saved(monkeypatch):
    return fake_saved(monkeypatch)


class TestBrand:

    @pytest.mark.parametrize('vendor_id, expected', [
        (0x10DE, 'NVIDIA'), (0x1002, 'AMD'), (0x8086, 'Intel'), (0x1234, 'PCI 1234'), (0x00AB, 'PCI 00ab'),
    ])
    def test_from_the_pci_vendor_id(self, vendor_id, expected):
        assert brand(card(KEY_ODD, 'Card', 'HIP_x_0000:41:00', 'HIP', vendor_id, 0x1)) == expected

    def test_an_unknown_vendor_is_named_by_its_id_not_guessed_from_blender(self):
        """The vendor id wins over the backend; an unlisted vendor is ``PCI <vvvv>``, never ``None``."""
        assert brand(card(KEY_ODD, 'Card', 'CUDA_x_0000:41:00', 'CUDA', 0x1AF4, 0x1)) == 'PCI 1af4'

    @pytest.mark.parametrize('backend, expected', [
        ('CUDA', 'NVIDIA'), ('OPTIX', 'NVIDIA'), ('HIP', 'AMD'), ('ONEAPI', 'Intel'), ('METAL', 'Apple'),
    ])
    def test_from_the_blender_backend_without_a_vendor_id(self, backend, expected):
        assert brand(card(KEY_ODD, 'Card', f'{backend}_x', backend)) == expected

    @pytest.mark.parametrize('vendor_id', [True, False, -1, 0x10000, 0x110DE])
    def test_a_vendor_id_that_is_not_a_pci_id_is_unknown(self, vendor_id):
        """As the manager: a bool or an id outside 0..0xFFFF is no vendor id; the backend decides, else unknown."""
        assert brand(card(KEY_ODD, 'Card', vendor_id=vendor_id, device_id=1)) is None
        assert brand(card(KEY_ODD, 'Card', 'HIP_x', 'HIP', vendor_id, 1)) == 'AMD'

    @pytest.mark.parametrize('blender_id, backend', [(None, None), ('VULKAN_x', 'VULKAN')])
    def test_unknown_without_vendor_id_or_known_backend(self, blender_id, backend):
        assert brand(card(KEY_ODD, 'Card', blender_id, backend)) is None

    def test_the_text_is_the_spec_sentence(self):
        assert MIXED_BRANDS_TEXT == ("Graphics cards of more than one brand ({brands}) cannot be ticked together on "
                                     "one machine; this is not supported. Tick the cards of one brand only.")
        assert brand_module.mixed_text(('AMD', 'NVIDIA')) == MIXED_AMD_NVIDIA


class TestMixedBrands:

    def test_one_brand_passes(self):
        assert mixed_brands([rtx_a(), rtx_b()]) is None
        assert mixed_brands([xt_03(), igpu()]) is None
        assert mixed_brands([]) is None

    def test_two_brands_are_named_sorted(self):
        assert mixed_brands([xt_03(), rtx_a(), rtx_b()]) == ('AMD', 'NVIDIA')

    def test_an_unknown_card_on_its_own_passes(self):
        assert mixed_brands([_unknown()]) is None

    def test_an_unknown_card_beside_a_known_one_is_refused(self):
        assert mixed_brands([rtx_a(), _unknown()]) == ('NVIDIA', 'unknown')

    def test_two_unknown_cards_are_refused(self):
        assert mixed_brands([_unknown(), _unknown(KEY_ODD_2)]) == ('unknown',)

    def test_the_sentence_names_known_brands_sorted_then_unknown_once(self):
        """As the manager prints it: sorted known brands, other vendors as ``PCI <lower-case hex>``, ``unknown`` last."""
        cards = [card(KEY_ODD, 'Odd', vendor_id=0x1AF4, device_id=1), rtx_a(), _unknown(KEY_ODD_2),
                 _unknown('pci:0000:43:00.0'), card('pci:0000:44:00.0', 'Arc', vendor_id=0x8086, device_id=2), xt_03()]
        assert brand_module.mixed_text(mixed_brands(cards)) == (
            "Graphics cards of more than one brand (AMD, Intel, NVIDIA, PCI 1af4, unknown) cannot be ticked together "
            "on one machine; this is not supported. Tick the cards of one brand only.")


class TestSaveRefusesMixedBrands:

    @pytest.mark.parametrize('method', ['gpu', 'cpu_gpu', 'cpu'])
    def test_two_brands_refused_whatever_the_method(self, saved, method):
        _state, writes = saved
        machine = inv(rtx_a(), xt_03())

        with pytest.raises(ValueError) as refused:
            store.save(method, 'split', [KEY_RTX_A, KEY_XT_03], [KEY_RTX_A, KEY_XT_03], machine, 0)

        assert str(refused.value) == MIXED_AMD_NVIDIA
        assert writes == []

    def test_one_brand_on_a_two_brand_machine_is_saved(self, saved):
        """A machine with NVIDIA cards and AMD integrated graphics: ticking the NVIDIA cards works."""
        state, _writes = saved
        machine = inv(rtx_a(), rtx_b(), igpu())

        assert store.save('gpu', 'split', [KEY_RTX_A, KEY_RTX_B], [KEY_RTX_A, KEY_RTX_B, KEY_IGPU], machine, 0) == 1
        assert [record['key'] for record in state['compute']['cards']] == [KEY_RTX_A, KEY_RTX_B]

    def test_an_unknown_card_on_its_own_is_saved(self, saved):
        machine = inv(_unknown(), rtx_a())
        assert store.save('gpu', 'split', [KEY_ODD], [KEY_ODD, KEY_RTX_A], machine, 0) == 1

    def test_an_unknown_card_beside_a_known_one_is_refused(self, saved):
        _state, writes = saved
        machine = inv(_unknown(), rtx_a())

        with pytest.raises(ValueError, match=r'more than one brand \(NVIDIA, unknown\)'):
            store.save('gpu', 'split', [KEY_ODD, KEY_RTX_A], [KEY_ODD, KEY_RTX_A], machine, 0)
        assert writes == []

    def test_two_unknown_cards_are_refused(self, saved):
        _state, writes = saved
        machine = inv(_unknown(), _unknown(KEY_ODD_2))

        with pytest.raises(ValueError, match=r'more than one brand \(unknown\) cannot'):
            store.save('gpu', 'split', [KEY_ODD, KEY_ODD_2], [KEY_ODD, KEY_ODD_2], machine, 0)
        assert writes == []
