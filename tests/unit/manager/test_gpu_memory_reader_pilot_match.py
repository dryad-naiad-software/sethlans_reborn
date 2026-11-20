# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests: the live card reading test and the pilot-result GPU cross-check
(gpu-memory-reader FR-GH-4, FR-GH-5, FR-GH-10, FR-GN-5).
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from workers.constants import UnrealKind
from workers.renderers import unreal_measured as um
from workers.services.pilot_result import TOTAL_SKEW_MB, _gpu_matches

KEY_A = 'pci:0000:0a:00.0'
KEY_B = 'pci:0000:0b:00.0'
KEY_C = 'pci:0000:0c:00.0'


def _stored_gpu(index, key, total):
    return {'index': index, 'name': 'Example Card', 'total_mb': total, 'used_mb': 700,
            'sethlans_used_mb': None, 'other_used_mb': None, 'card_key': key}


def _reading(*gpus, card=True):
    return {'schema': 1, 'source': 'mixed', 'age_s': 1,
            'capabilities': {'card': card, 'process': False},
            'gpus': list(gpus) if gpus else [_stored_gpu(0, KEY_A, 16304), _stored_gpu(1, KEY_B, 24560)]}


def _pilot_gpu(index, key, total):
    return {'index': index, 'name': 'Example Card', 'total_mb': total, 'card_key': key}


class TestReadingMeasuresCard:

    @pytest.mark.parametrize('gpu_memory', [
        {'capabilities': {'card': True, 'process': False}},
        {'capabilities': {'card': True}},
        _reading(),
    ])
    def test_true_only_for_stored_card_true(self, gpu_memory):
        assert um.reading_measures_card(gpu_memory) is True

    @pytest.mark.parametrize('gpu_memory', [
        None, {}, [], 'x', {'source': 'nvidia-smi', 'gpus': [_stored_gpu(0, KEY_A, 1)]},
        {'capabilities': None}, {'capabilities': []}, {'capabilities': {}},
        {'capabilities': {'card': False, 'process': True}}, {'capabilities': {'card': 1}},
        {'capabilities': {'card': 'true'}}, {'capabilities': {'process': True}},
    ])
    def test_false_otherwise(self, gpu_memory):
        assert um.reading_measures_card(gpu_memory) is False

    @pytest.mark.parametrize('gpu_memory, expected', [
        (_reading(), True), (_reading(card=False), False), ({}, False), (None, False),
        ({'source': 'nvidia-smi', 'schema': 1, 'age_s': 1, 'gpus': []}, False),
    ])
    def test_measures_card_reads_the_worker(self, gpu_memory, expected):
        assert um.measures_card(SimpleNamespace(gpu_memory=gpu_memory)) is expected


class TestMeasuredClaimRejection:

    def test_message_text(self):
        assert um.NO_CARD_READING_MESSAGE == "Worker cannot measure GPU memory (no live card reading)."

    def test_pilot_without_card_reading_is_refused_before_the_query(self):
        worker = SimpleNamespace(gpu_memory=_reading(card=False))
        job = SimpleNamespace(unreal_kind=UnrealKind.PILOT)
        with patch.object(um, 'claim_row') as claim_row:
            assert um.measured_claim_rejection(worker, job) == um.NO_CARD_READING_MESSAGE
        claim_row.assert_not_called()

    def test_pilot_with_card_reading_goes_on_to_the_row(self):
        worker = SimpleNamespace(gpu_memory=_reading())
        job = SimpleNamespace(unreal_kind=UnrealKind.PILOT)
        with patch.object(um, 'claim_row', return_value=None) as claim_row:
            assert um.measured_claim_rejection(worker, job) is None
        claim_row.assert_called_once_with(job)

    def test_normal_job_ignores_the_card_reading(self):
        worker = SimpleNamespace(gpu_memory={})
        job = SimpleNamespace(unreal_kind=UnrealKind.RENDER)
        with patch.object(um, 'claim_row', return_value=None) as claim_row:
            assert um.measured_claim_rejection(worker, job) is None
        claim_row.assert_called_once_with(job)


class TestGpuMatches:

    def test_same_key_and_total_passes(self):
        assert _gpu_matches(_reading(), _pilot_gpu(0, KEY_A, 16304)) is True

    def test_same_index_with_another_key_fails(self):
        assert _gpu_matches(_reading(), _pilot_gpu(0, KEY_C, 16304)) is False

    def test_same_index_with_the_other_cards_key_fails_on_total(self):
        assert _gpu_matches(_reading(), _pilot_gpu(0, KEY_B, 16304)) is False

    @pytest.mark.parametrize('index', [1, 7, 63])
    def test_another_index_with_the_same_key_passes(self, index):
        assert _gpu_matches(_reading(), _pilot_gpu(index, KEY_A, 16304)) is True

    def test_stored_null_key_never_matches(self):
        reading = _reading(_stored_gpu(0, None, 16304), _stored_gpu(1, None, 24560))
        assert _gpu_matches(reading, _pilot_gpu(0, KEY_A, 16304)) is False

    def test_null_key_on_both_sides_does_not_match(self):
        reading = _reading(_stored_gpu(0, None, 16304))
        assert _gpu_matches(reading, _pilot_gpu(0, None, 16304)) is False

    def test_missing_stored_key_never_matches(self):
        stored = _stored_gpu(0, KEY_A, 16304)
        del stored['card_key']
        assert _gpu_matches(_reading(stored), _pilot_gpu(0, KEY_A, 16304)) is False

    @pytest.mark.parametrize('skew, expected', [
        (0, True), (TOTAL_SKEW_MB, True), (-TOTAL_SKEW_MB, True),
        (TOTAL_SKEW_MB + 1, False), (-TOTAL_SKEW_MB - 1, False),
    ])
    def test_total_tolerance(self, skew, expected):
        assert TOTAL_SKEW_MB == 64
        assert _gpu_matches(_reading(), _pilot_gpu(0, KEY_A, 16304 + skew)) is expected

    def test_card_false_fails(self):
        assert _gpu_matches(_reading(card=False), _pilot_gpu(0, KEY_A, 16304)) is False

    def test_no_capabilities_fails_even_for_nvidia_smi(self):
        reading = _reading()
        del reading['capabilities']
        reading['source'] = 'nvidia-smi'
        assert _gpu_matches(reading, _pilot_gpu(0, KEY_A, 16304)) is False

    @pytest.mark.parametrize('gpu_memory', [None, {}, [], 'x'])
    def test_non_reading_fails(self, gpu_memory):
        assert _gpu_matches(gpu_memory, _pilot_gpu(0, KEY_A, 16304)) is False

    @pytest.mark.parametrize('gpus', [None, [], ['junk', 5], [_stored_gpu(0, KEY_A, None)]])
    def test_unusable_stored_gpus_fail(self, gpus):
        reading = _reading()
        reading['gpus'] = gpus
        assert _gpu_matches(reading, _pilot_gpu(0, KEY_A, 16304)) is False

    def test_junk_entry_before_a_match_is_skipped(self):
        reading = _reading()
        reading['gpus'] = ['junk', _stored_gpu(3, KEY_A, 16304)]
        assert _gpu_matches(reading, _pilot_gpu(0, KEY_A, 16304)) is True
