# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the card-choice rules (worker card selection FR-CM-10
steps 3-5, FR-CC-4, D19, AC-26, AC-34, AC-43): every shape rule and its
order, the state checks and their order, the one-family rule and the
rules against the stored report.
"""

import pytest
from django.http import QueryDict

from tests.unit.manager.card_report_helpers import KEY, KEY_2, alert, card, stored
from workers.services import card_choice_rules as rules
from workers.services.card_brand import MIXED_BRANDS_TEXT

NV = {'vendor_id': 0x10DE, 'device_id': 0x2B85}
AMD = {'vendor_id': 0x1002, 'device_id': 0x164E}


def _body(**overrides):
    values = {'method': 'gpu', 'gpu_mode': 'split', 'cards': [{'key': KEY, **NV}],
              'shown': [KEY, KEY_2], 'base_revision': 3}
    values.update(overrides)
    return values


def _keys(count):
    return [f'pci:0000:{n:02x}:00.0' for n in range(count)]


TWO = [{'key': KEY, **NV}, {'key': KEY_2, **AMD}]
# Two cards of one brand (Unreal render per card FR-PC-BR-5).
NV_2 = {'vendor_id': 0x10DE, 'device_id': 0x2684}
TWO_NV = [{'key': KEY, **NV}, {'key': KEY_2, **NV_2}]


class TestShape:

    def test_valid_bodies_pass(self):
        assert rules.shape_error(_body()) is None
        assert rules.shape_error(_body(method='cpu', cards=[], shown=[])) is None
        assert rules.shape_error(_body(gpu_mode='combined', cards=TWO)) is None
        assert rules.shape_error(_body(cards=[{'key': 'apple-gpu', 'vendor_id': None, 'device_id': None}],
                                       shown=['apple-gpu'])) is None
        assert rules.shape_error(_body(base_revision=2 ** 31 - 1)) is None

    @pytest.mark.parametrize('data', [
        [], 'x', None, QueryDict('method=gpu'), {k: v for k, v in _body().items() if k != 'shown'},
        {**_body(), 'extra': 1}, {**_body(), 'id': 'a' * 32},
    ])
    def test_bad_body(self, data):
        assert rules.shape_error(data) == rules.BAD_BODY

    @pytest.mark.parametrize('overrides, sentence', [
        ({'method': 'GPU'}, rules.BAD_METHOD),
        ({'method': None}, rules.BAD_METHOD),
        ({'gpu_mode': 'both'}, rules.BAD_GPU_MODE),
        ({'base_revision': True}, rules.BAD_REVISION),
        ({'base_revision': -1}, rules.BAD_REVISION),
        ({'base_revision': 2 ** 31}, rules.BAD_REVISION),
        ({'base_revision': 3.0}, rules.BAD_REVISION),
        ({'base_revision': '3'}, rules.BAD_REVISION),
        ({'cards': {'key': KEY}}, rules.BAD_CARDS),
        ({'cards': [{'key': k, **NV} for k in _keys(17)], 'shown': _keys(16)}, rules.BAD_CARDS),
        ({'cards': [{'key': KEY, 'vendor_id': 1}]}, rules.BAD_CARDS),
        ({'cards': [{'key': KEY, **NV, 'name': 'x'}]}, rules.BAD_CARDS),
        ({'cards': [KEY]}, rules.BAD_CARDS),
        ({'cards': [{'key': 'PCI:0000:01:00.0', **NV}]}, rules.BAD_KEY),
        ({'cards': [{'key': 'pci:0000:01:00', **NV}]}, rules.BAD_KEY),
        ({'cards': [{'key': 7, **NV}]}, rules.BAD_KEY),
        ({'cards': [{'key': KEY, 'vendor_id': True, 'device_id': 1}]}, rules.BAD_IDS),
        ({'cards': [{'key': KEY, 'vendor_id': 0x10000, 'device_id': 1}]}, rules.BAD_IDS),
        ({'cards': [{'key': KEY, 'vendor_id': 1, 'device_id': -1}]}, rules.BAD_IDS),
        ({'cards': [{'key': KEY, 'vendor_id': '10de', 'device_id': 1}]}, rules.BAD_IDS),
        ({'cards': [{'key': KEY, **NV}, {'key': KEY, **NV}]}, rules.DUPLICATE_CARD),
        ({'shown': KEY}, rules.BAD_SHOWN),
        ({'shown': _keys(17)}, rules.BAD_SHOWN),
        ({'shown': [KEY, KEY]}, rules.BAD_SHOWN),
        ({'shown': [KEY, 'gpu0']}, rules.BAD_SHOWN),
        ({'shown': [KEY_2]}, rules.NOT_SHOWN),
        ({'cards': [], 'shown': []}, rules.GPU_NEEDS_CARD),
        ({'method': 'cpu_gpu', 'cards': []}, rules.GPU_NEEDS_CARD),
        ({'gpu_mode': 'combined'}, rules.COMBINED_NEEDS_TWO),
    ])
    def test_each_rule(self, overrides, sentence):
        assert rules.shape_error(_body(**overrides)) == sentence

    @pytest.mark.parametrize('overrides, sentence', [
        ({'method': 'x', 'gpu_mode': 'x'}, rules.BAD_METHOD),
        ({'gpu_mode': 'x', 'base_revision': True}, rules.BAD_GPU_MODE),
        ({'base_revision': True, 'cards': 'x'}, rules.BAD_REVISION),
        ({'cards': [{'key': 'bad'}], 'shown': 'x'}, rules.BAD_CARDS),
        ({'cards': [{'key': 'bad', 'vendor_id': True, 'device_id': 1}]}, rules.BAD_KEY),
        ({'cards': [{'key': KEY, 'vendor_id': True, 'device_id': 1}] * 2}, rules.BAD_IDS),
        ({'cards': [{'key': KEY, **NV}] * 2, 'shown': 'x'}, rules.DUPLICATE_CARD),
        ({'shown': [KEY_2, KEY_2]}, rules.BAD_SHOWN),
        ({'shown': [], 'gpu_mode': 'combined'}, rules.NOT_SHOWN),
        ({'cards': [], 'gpu_mode': 'combined'}, rules.GPU_NEEDS_CARD),
    ])
    def test_first_broken_rule_decides(self, overrides, sentence):
        assert rules.shape_error(_body(**overrides)) == sentence

    def test_clean_body_keeps_only_the_known_keys_in_order(self):
        body = rules.clean_body(_body())
        assert list(body) == list(rules.BODY_KEYS)
        assert body['cards'] == [{'key': KEY, **NV}]


class TestState:

    @pytest.mark.parametrize('card_status, base, sentence', [
        ({}, 3, rules.NO_REPORT),
        (None, 3, rules.NO_REPORT),
        (stored(schema=2), 3, rules.NO_REPORT),
        (stored(env_override=True, manager_changes_allowed=False, revision=9), 3, rules.ENV_OVERRIDE),
        (stored(manager_changes_allowed=False, revision=9), 3, rules.NOT_ALLOWED),
        ({k: v for k, v in stored().items() if k != 'manager_changes_allowed'}, 3, rules.NOT_ALLOWED),
        (stored(revision=4), 3, rules.STALE),
        ({k: v for k, v in stored().items() if k != 'revision'}, 3, rules.STALE),
        (stored(), 3, None),
    ])
    def test_state_checks_in_order(self, card_status, base, sentence):
        assert rules.state_error(card_status, base) == sentence


class TestOneFamily:

    @pytest.mark.parametrize('backends, ok', [
        (['CUDA', 'OPTIX'], True), (['OPTIX', 'OPTIX'], True), (['HIP', 'HIP'], True),
        (['HIP', 'HIP', None], True), (['CUDA', 'HIP'], False), (['CUDA', None], False),
        (['CUDA'], False), ([None, None], False), (['ONEAPI', 'HIP'], False), (['METAL', 'METAL'], True),
    ])
    def test_rule(self, backends, ok):
        assert rules.one_family(backends) is ok


def _status(cards, alerts=()):
    return stored(cards=cards, alerts=list(alerts), notices=[], warnings=[])


AMD_CARD = card(key=KEY_2, name='AMD', ticked=False, backend='HIP', **AMD)
NV_CARD_2 = card(key=KEY_2, name='NVIDIA 2', ticked=False, backend='OPTIX', **NV_2)


class TestReport:

    def test_valid(self):
        assert rules.report_error(_body(), _status([card(), AMD_CARD])) is None

    def test_shown_key_not_reported(self):
        assert rules.report_error(_body(), _status([card()])) == rules.NOT_REPORTED.format(key=KEY_2)

    @pytest.mark.parametrize('ids', [{'vendor_id': 0x10DE, 'device_id': 1}, {'vendor_id': None, 'device_id': None}])
    def test_ticked_ids_must_match(self, ids):
        error = rules.report_error(_body(cards=[{'key': KEY, **ids}]), _status([card(), AMD_CARD]))
        assert error == rules.IDS_DIFFER.format(key=KEY)

    def test_null_ids_match_null_ids(self):
        nulls = {'vendor_id': None, 'device_id': None}
        assert rules.report_error(_body(cards=[{'key': KEY, **nulls}]), _status([card(**nulls), AMD_CARD])) is None

    def test_combined_of_two_brands_is_refused_as_mixed_brands_first(self):
        """The brand check (FR-PC-BR-5) comes before the one-family rule."""
        error = rules.report_error(_body(gpu_mode='combined', cards=TWO), _status([card(), AMD_CARD]))
        assert error == MIXED_BRANDS_TEXT.format(brands='AMD, NVIDIA')

    def test_combined_with_a_null_backend_is_refused(self):
        """AC-34: one CUDA card and one card Blender does not drive."""
        cards = [card(backend='CUDA'), card(key=KEY_2, ticked=False, backend=None, blender=False, **NV_2)]
        assert rules.report_error(_body(gpu_mode='combined', cards=TWO_NV), _status(cards)) == rules.ONE_FAMILY

    def test_combined_of_one_family_passes(self):
        cards = [card(backend='CUDA'), card(key=KEY_2, ticked=False, backend='OPTIX', **NV_2)]
        assert rules.report_error(_body(gpu_mode='combined', cards=TWO_NV), _status(cards)) is None

    @pytest.mark.parametrize('state', ['lost', 'changed', 'held', 'blender_lost'])
    def test_newly_ticking_a_held_state_is_refused(self, state):
        cards = [card(), card(key=KEY_2, ticked=False, state=state, **NV_2)]
        error = rules.report_error(_body(cards=TWO_NV), _status(cards))
        assert error == rules.HELD.format(key=KEY_2)

    @pytest.mark.parametrize('kind', ['card_lost', 'card_changed', 'card_lost_blender'])
    def test_newly_ticking_a_card_named_by_a_holding_alert_is_refused(self, kind):
        error = rules.report_error(_body(cards=TWO_NV), _status([card(), NV_CARD_2], [alert(key=KEY_2, kind=kind)]))
        assert error == rules.HELD.format(key=KEY_2)

    def test_adapter_mismatch_holds_nothing(self):
        status = _status([card(), NV_CARD_2], [alert(key=KEY_2, kind='adapter_mismatch')])
        assert rules.report_error(_body(cards=TWO_NV), status) is None

    def test_split_of_two_brands_is_refused(self):
        error = rules.report_error(_body(cards=TWO), _status([card(), AMD_CARD]))
        assert error == MIXED_BRANDS_TEXT.format(brands='AMD, NVIDIA')

    def test_mixed_brands_comes_after_the_ids_check(self):
        cards = [{'key': KEY, 'vendor_id': 0x10DE, 'device_id': 1}, {'key': KEY_2, **AMD}]
        assert rules.report_error(_body(cards=cards), _status([card(), AMD_CARD])) == rules.IDS_DIFFER.format(key=KEY)

    def test_mixed_brands_comes_before_the_held_check(self):
        status = _status([card(), AMD_CARD], [alert(key=KEY_2, kind='card_lost')])
        assert rules.report_error(_body(cards=TWO), status) == MIXED_BRANDS_TEXT.format(brands='AMD, NVIDIA')

    def test_keeping_a_ticked_held_card_ticked_is_accepted(self):
        """AC-26: the card stays held on the worker; only a new tick is refused."""
        status = _status([card(state='held'), AMD_CARD], [alert(key=KEY, kind='card_changed')])
        assert rules.report_error(_body(), status) is None
