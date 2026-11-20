# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests: the heartbeat ``gpu_memory`` validator for the GPU memory reader
(gpu-memory-reader FR-GH-1, FR-GH-2, FR-GH-3, FR-GT-4, AC-26).
"""

import pytest

from workers.views import heartbeat_unreal as hu

KEY_A = 'pci:0000:0a:00.0'
KEY_B = 'pci:0000:0b:00.0'
NEW_SOURCES = ('pdh', 'amdgpu-sysfs', 'mixed')


def _gpu(**extra):
    gpu = {'index': 0, 'name': 'Example Card 16G', 'total_mb': 16304, 'used_mb': 900,
           'sethlans_used_mb': None, 'other_used_mb': None, 'card_key': KEY_A}
    gpu.update(extra)
    return gpu


def _mem(*gpus, **extra):
    mem = {'schema': 1, 'source': 'pdh', 'age_s': 2,
           'capabilities': {'card': True, 'process': False},
           'gpus': list(gpus) if gpus else [_gpu()]}
    mem.update(extra)
    return mem


def _stored(raw):
    return hu._validate_gpu_memory(raw)


class TestSources:

    def test_source_set_is_the_spec_set(self):
        assert hu.GPU_MEMORY_SOURCES == frozenset({'nvidia-smi', 'override', 'pdh', 'amdgpu-sysfs', 'mixed'})

    @pytest.mark.parametrize('source', ['nvidia-smi', 'pdh', 'amdgpu-sysfs', 'mixed'])
    def test_card_sources_are_accepted(self, source):
        stored = _stored(_mem(source=source))
        assert stored['source'] == source
        assert stored['capabilities'] == {'card': True, 'process': False}
        assert stored['gpus'] == [_gpu()]

    def test_override_source_is_accepted(self):
        stored = _stored(_mem(source='override'))
        assert stored['source'] == 'override'
        assert stored['gpus'] == [_gpu()]

    @pytest.mark.parametrize('source', ['rocm', 'PDH', 'nvidia_smi', 'mixed ', '', None, 5, True])
    def test_unknown_source_rejects_the_whole_reading(self, source):
        assert _stored(_mem(source=source)) == {}

    @pytest.mark.parametrize('source', [['pdh'], {'pdh': 1}])
    def test_unhashable_source_rejects_the_whole_reading(self, source):
        """#279: a list or dict source was a TypeError (500), not an invalid value."""
        assert _stored(_mem(source=source)) == {}

    @pytest.mark.parametrize('source', [['launcher'], {'launcher': 1}])
    def test_unhashable_install_source_drops_the_install(self, source):
        """#279: the same rule for an Unreal install's ``source``."""
        install = {'build': '5.8.4-58902052', 'supported': True, 'source': source}
        assert hu._valid_install(install) is None

    def test_schema_must_still_be_one(self):
        assert _stored(_mem(schema=2)) == {}

    def test_stored_keys_are_rebuilt_from_known_keys_only(self):
        stored = _stored(_mem(evil=1, schema_version=2))
        assert set(stored) == {'schema', 'source', 'age_s', 'capabilities', 'gpus'}
        assert stored['schema'] == 1


class TestCapabilities:

    @pytest.mark.parametrize('card, process', [(True, True), (True, False), (False, True), (False, False)])
    def test_valid_capabilities_are_stored(self, card, process):
        stored = _stored(_mem(capabilities={'card': card, 'process': process}))
        assert stored['capabilities'] == {'card': card, 'process': process}

    def test_absent_capabilities_are_not_stored(self):
        raw = _mem()
        del raw['capabilities']
        stored = _stored(raw)
        assert 'capabilities' not in stored
        assert stored['gpus'] == [_gpu()]

    @pytest.mark.parametrize('caps', [
        None, [], 'card', 1, True,
        {}, {'card': True}, {'process': True},
        {'card': 1, 'process': True}, {'card': True, 'process': 0},
        {'card': 'true', 'process': True}, {'card': True, 'process': None},
    ])
    def test_wrong_type_capabilities_are_not_stored_and_reading_kept(self, caps):
        stored = _stored(_mem(capabilities=caps))
        assert 'capabilities' not in stored
        assert stored['source'] == 'pdh'
        assert stored['gpus'] == [_gpu()]

    def test_extra_capability_keys_are_dropped(self):
        stored = _stored(_mem(capabilities={'card': True, 'process': True, 'evil': True}))
        assert stored['capabilities'] == {'card': True, 'process': True}


class TestCardForcedFalse:
    """AC-26: the manager never trusts ``card: true`` as sent."""

    @pytest.mark.parametrize('process', [True, False])
    def test_override_with_card_true_is_stored_false(self, process):
        stored = _stored(_mem(source='override', capabilities={'card': True, 'process': process}))
        assert stored['capabilities'] == {'card': False, 'process': process}

    @pytest.mark.parametrize('process', [True, False])
    def test_every_gpu_dropped_stores_card_false(self, process):
        raw = _mem(_gpu(index=64), _gpu(index=1, used_mb=99999), 'junk',
                   capabilities={'card': True, 'process': process})
        stored = _stored(raw)
        assert stored['gpus'] == []
        assert stored['capabilities'] == {'card': False, 'process': process}

    def test_empty_gpu_list_stores_card_false(self):
        stored = _stored(_mem(gpus=[]))
        assert stored['gpus'] == []
        assert stored['capabilities'] == {'card': False, 'process': False}

    def test_one_valid_gpu_left_keeps_card_true(self):
        stored = _stored(_mem(_gpu(index=64), _gpu(index=1)))
        assert stored['gpus'] == [_gpu(index=1)]
        assert stored['capabilities']['card'] is True

    @pytest.mark.parametrize('keys', [[None], ['apple-gpu'], ['bad', None], [KEY_A, KEY_A]])
    def test_no_kept_gpu_with_a_card_key_stores_card_false(self, keys):
        """Only the second entry of a duplicate pair loses its key, so [KEY_A, KEY_A] stays true."""
        gpus = [_gpu(index=i, card_key=key) for i, key in enumerate(keys)]
        stored = _stored(_mem(*gpus))
        assert len(stored['gpus']) == len(keys)
        assert stored['capabilities']['card'] is (keys == [KEY_A, KEY_A])

    def test_one_keyed_gpu_among_keyless_keeps_card_true(self):
        stored = _stored(_mem(_gpu(index=0, card_key=None), _gpu(index=1, card_key=KEY_B)))
        assert stored['capabilities'] == {'card': True, 'process': False}

    def test_card_false_is_never_raised_to_true(self):
        stored = _stored(_mem(capabilities={'card': False, 'process': True}))
        assert stored['capabilities'] == {'card': False, 'process': True}


class TestCardKey:

    @pytest.mark.parametrize('key', [KEY_A, 'pci:0000abcd:ff:1f.7', 'pci:00001:00:00.0'])
    def test_valid_key_is_kept(self, key):
        assert _stored(_mem(_gpu(card_key=key)))['gpus'][0]['card_key'] == key

    @pytest.mark.parametrize('key', [
        'apple-gpu', 'Apple-GPU', 'PCI:0000:0a:00.0', 'pci:0000:0A:00.0', 'pci:000:0a:00.0',
        'pci:000000000:0a:00.0', 'pci:0000:0a:00.8', 'pci:0000:a:00.0', 'pci:0000:0a:000.0',
        KEY_A + '\n', ' ' + KEY_A, '', None, 5, True, ['pci'], {'k': KEY_A},
    ])
    def test_invalid_key_becomes_null_and_keeps_the_gpu(self, key):
        gpus = _stored(_mem(_gpu(card_key=key)))['gpus']
        assert gpus == [_gpu(card_key=None)]

    def test_absent_key_is_stored_as_null(self):
        raw = _gpu()
        del raw['card_key']
        gpus = _stored(_mem(raw))['gpus']
        assert gpus == [_gpu(card_key=None)]

    def test_duplicate_key_keeps_the_first_and_nulls_the_rest(self):
        raw = _mem(_gpu(index=0), _gpu(index=1), _gpu(index=2, card_key=KEY_B), _gpu(index=3))
        keys = [gpu['card_key'] for gpu in _stored(raw)['gpus']]
        assert keys == [KEY_A, None, KEY_B, None]

    def test_duplicate_key_does_not_change_card_capability(self):
        stored = _stored(_mem(_gpu(index=0), _gpu(index=1)))
        assert len(stored['gpus']) == 2
        assert stored['capabilities']['card'] is True

    def test_dropped_entry_does_not_reserve_its_key(self):
        raw = _mem(_gpu(index=64), _gpu(index=1))
        assert _stored(raw)['gpus'] == [_gpu(index=1)]

    def test_invalid_first_key_does_not_block_a_later_valid_one(self):
        raw = _mem(_gpu(index=0, card_key='apple-gpu'), _gpu(index=1))
        assert [gpu['card_key'] for gpu in _stored(raw)['gpus']] == [None, KEY_A]

    def test_every_stored_entry_has_the_card_key_key(self):
        bare = _gpu(index=2)
        del bare['card_key']
        raw = _mem(_gpu(index=0), _gpu(index=1), bare, _gpu(index=3, card_key='apple-gpu'),
                   _gpu(index=4, card_key=KEY_B))
        gpus = _stored(raw)['gpus']
        assert len(gpus) == 5
        assert all('card_key' in gpu for gpu in gpus)
        assert [gpu['card_key'] for gpu in gpus] == [KEY_A, None, None, None, KEY_B]

    def test_override_reading_keeps_its_card_keys(self):
        gpus = _stored(_mem(source='override'))['gpus']
        assert gpus[0]['card_key'] == KEY_A
