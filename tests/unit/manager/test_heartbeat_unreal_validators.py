# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests: validators for the Unreal heartbeat keys (Unreal foundation
FR-RT-7, FR-RT-12, FR-RT-16, FR-RT-17).
"""

from unittest.mock import patch

import pytest

from workers.views import heartbeat_unreal as hu

BUILD = '5.8.4-58902052'
SHARE = r'\\alpha.lan\share'


def _caps(**extra):
    caps = {
        'schema': 1, 'enabled': True, 'disabled_reason': 'none',
        'installs': [{'build': BUILD, 'supported': True, 'source': 'launcher'}],
        'share': SHARE, 'share_reachable': True,
    }
    caps.update(extra)
    return caps


def _gpu(**extra):
    gpu = {'index': 0, 'name': 'NVIDIA GeForce RTX 5090', 'total_mb': 32607, 'used_mb': 1012,
           'sethlans_used_mb': 0, 'other_used_mb': 1012}
    gpu.update(extra)
    return gpu


def _mem(*gpus, **extra):
    mem = {'schema': 1, 'source': 'nvidia-smi', 'age_s': 4, 'gpus': list(gpus) or [_gpu()]}
    mem.update(extra)
    return mem


class TestSanitizeReason:

    @pytest.mark.parametrize('raw, expected', [
        ("invalid SETHLANS_UNREAL_SHARE: component 'a:b' (rule 4)",
         "invalid SETHLANS_UNREAL_SHARE: component 'a:b' (rule 4)"),
        ('<script>`x`</script>', '?script??x??/script?'),
        ('tab\there\u00e9\u202e', 'tab?here??'),
        (5, ''), (None, ''),
    ])
    def test_replaces_unsafe_characters(self, raw, expected):
        assert hu._sanitize_reason(raw) == expected

    def test_truncates(self):
        assert hu._sanitize_reason('x' * 300) == 'x' * 200


class TestUnrealCapabilities:

    def test_valid_payload_round_trips(self):
        assert hu._validate_unreal_capabilities(_caps()) == _caps()

    @pytest.mark.parametrize('raw', [
        None, [], 'x', _caps(schema=2), _caps(schema=True), _caps(enabled='yes'),
        _caps(installs='x'), _caps(installs=[{}] * 17),
    ])
    def test_bad_shapes_yield_empty(self, raw):
        assert hu._validate_unreal_capabilities(raw) == {}

    def test_bad_installs_are_dropped_at_debug(self):
        installs = [
            {'build': BUILD, 'supported': True, 'source': 'launcher'},
            {'build': '5.8.4', 'supported': True, 'source': 'launcher'},
            {'build': BUILD, 'supported': 1, 'source': 'launcher'},
            {'build': BUILD, 'supported': True, 'source': 'registry'},
            'junk',
        ]
        with patch.object(hu, 'logger') as log:
            caps = hu._validate_unreal_capabilities(_caps(installs=installs))
        assert caps['installs'] == [installs[0]]
        assert log.debug.call_count == 4
        log.warning.assert_not_called()

    @pytest.mark.parametrize('share', [r'\\ALPHA.lan\share', 'C:\\x', 5, '\\\\h\\' + 's' * 1100])
    def test_non_canonical_or_invalid_share_is_stored_empty(self, share):
        assert hu._validate_unreal_capabilities(_caps(share=share))['share'] == ''

    def test_non_bool_reachable_is_false(self):
        assert hu._validate_unreal_capabilities(_caps(share_reachable='true'))['share_reachable'] is False

    def test_share_keys_are_optional(self):
        raw = _caps()
        del raw['share'], raw['share_reachable']
        assert 'share' not in hu._validate_unreal_capabilities(raw)

    def test_extra_keys_are_dropped_and_reason_sanitized(self):
        caps = hu._validate_unreal_capabilities(_caps(evil=1, disabled_reason='<b>'))
        assert 'evil' not in caps
        assert caps['disabled_reason'] == '?b?'


class TestGpuMemory:

    def test_valid_payload_round_trips(self):
        assert hu._validate_gpu_memory(_mem()) == _mem()

    @pytest.mark.parametrize('raw', [
        None, [], _mem(schema=2), _mem(schema=True), _mem(source='rocm'), _mem(age_s=-1),
        _mem(age_s=86401), _mem(age_s=True), _mem(gpus='x'), _mem(*[_gpu(index=i) for i in range(17)]),
    ])
    def test_bad_top_level_yields_empty(self, raw):
        assert hu._validate_gpu_memory(raw) == {}

    @pytest.mark.parametrize('gpu', [
        _gpu(index=64), _gpu(index=True), _gpu(total_mb=1_048_577), _gpu(used_mb=40000),
        _gpu(total_mb=True), _gpu(used_mb=-1), 'junk',
    ])
    def test_bad_gpu_is_dropped(self, gpu):
        assert hu._validate_gpu_memory(_mem(gpu))['gpus'] == []

    def test_duplicate_index_keeps_the_first(self):
        first, second = _gpu(), _gpu(name='Other')
        assert hu._validate_gpu_memory(_mem(first, second))['gpus'] == [first]

    @pytest.mark.parametrize('extra', [
        {'sethlans_used_mb': 30000, 'other_used_mb': 3000},
        {'sethlans_used_mb': -1}, {'other_used_mb': True}, {'other_used_mb': 40000},
    ])
    def test_bad_attribution_becomes_null_and_keeps_the_gpu(self, extra):
        gpus = hu._validate_gpu_memory(_mem(_gpu(**extra)))['gpus']
        assert len(gpus) == 1
        assert (gpus[0]['sethlans_used_mb'], gpus[0]['other_used_mb']) == (None, None)

    def test_attribution_skew_is_tolerated(self):
        gpu = _gpu(total_mb=1000, used_mb=1000, sethlans_used_mb=600, other_used_mb=464)
        assert hu._validate_gpu_memory(_mem(gpu))['gpus'][0]['other_used_mb'] == 464

    def test_unsafe_name_is_blanked(self):
        assert hu._validate_gpu_memory(_mem(_gpu(name='<b>')))['gpus'][0]['name'] == ''
        assert hu._validate_gpu_memory(_mem(_gpu(name='x' * 129)))['gpus'][0]['name'] == ''


class TestActiveJobIdsAndBootId:

    @pytest.mark.parametrize('raw, expected', [
        ([], []), ([3, 1], [3, 1]), ([1] * 256, [1] * 256),
        ([1] * 257, None), ('1', None), ([0], None), ([True], None), (['1'], None), (None, None),
        ([2 ** 31 - 1], [2 ** 31 - 1]), ([2 ** 31], None), ([10 ** 30], None), ([-1], None),
    ])
    def test_active_job_ids(self, raw, expected):
        assert hu._validate_active_job_ids(raw) == expected

    @pytest.mark.parametrize('raw, expected', [
        ('a' * 32, 'a' * 32), ('A' * 32, None), ('a' * 31, None), ('a' * 32 + '\n', None),
        (5, None), (None, None),
    ])
    def test_boot_id(self, raw, expected):
        assert hu._validate_boot_id(raw) == expected
