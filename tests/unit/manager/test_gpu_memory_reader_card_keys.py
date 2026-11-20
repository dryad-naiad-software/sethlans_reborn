# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests: the ``card_keys`` leaf module, ``PilotGpuSerializer.card_key``
and the pinned ``gpu_memory.source`` enum (gpu-memory-reader FR-GH-3,
FR-GH-6, FR-GH-10).
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.utils.module_loading import import_string

from workers import card_keys
from workers.card_keys import APPLE_GPU_KEY, is_card_key, is_pci_card_key
from workers.serializers import workers as worker_serializers
from workers.serializers.unreal_measurement import PilotGpuSerializer
from workers.views import heartbeat_unreal

MANAGER_DIR = Path(__file__).resolve().parents[3] / 'manager'
SOURCES = ('amdgpu-sysfs', 'mixed', 'nvidia-smi', 'override', 'pdh')

VALID_PCI = [
    'pci:0000:01:00.0', 'pci:0000:ff:1f.7', 'pci:00000:00:00.0', 'pci:0000abcd:0a:0b.3',
    'pci:ffffffff:ff:ff.7',
]
INVALID = [
    'pci:000:01:00.0', 'pci:000000000:01:00.0', 'pci:0000:1:00.0', 'pci:0000:001:00.0',
    'pci:0000:01:0.0', 'pci:0000:01:00.8', 'pci:0000:01:00.', 'pci:0000:01:00.00',
    'PCI:0000:01:00.0', 'pci:0000:0A:00.0', 'pci:000g:01:00.0', 'pci:0000-01:00.0',
    'pci:0000:01:00.0\n', ' pci:0000:01:00.0', 'pci:０000:01:00.0', 'Apple-GPU', 'apple-gpu ',
    '', None, 0, True, b'pci:0000:01:00.0', ['pci:0000:01:00.0'],
]


class TestIsPciCardKey:

    @pytest.mark.parametrize('value', VALID_PCI)
    def test_valid_key(self, value):
        assert is_pci_card_key(value) is True

    @pytest.mark.parametrize('value', INVALID + [APPLE_GPU_KEY])
    def test_invalid_key_and_apple_gpu(self, value):
        assert is_pci_card_key(value) is False


class TestIsCardKey:

    @pytest.mark.parametrize('value', VALID_PCI + ['apple-gpu'])
    def test_valid_key(self, value):
        assert is_card_key(value) is True

    @pytest.mark.parametrize('value', INVALID)
    def test_invalid_key(self, value):
        assert is_card_key(value) is False


class TestLeafModule:

    def test_imports_only_re(self):
        tree = ast.parse(Path(card_keys.__file__).read_text(encoding='utf-8'))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add('.' * node.level + (node.module or ''))
        assert imported == {'re'}

    def test_import_loads_no_view_module(self):
        code = (
            'import json, sys\n'
            f'sys.path.insert(0, {str(MANAGER_DIR)!r})\n'
            'import workers.card_keys\n'
            'print(json.dumps(sorted(m for m in sys.modules if m.split(".")[0] == "workers")))\n'
        )
        result = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True, text=True,
                                timeout=60, check=False)
        assert result.returncode == 0, result.stderr
        loaded = json.loads(result.stdout)
        assert loaded == ['workers', 'workers.card_keys']

    def test_view_modules_share_the_leaf_functions(self):
        from workers.views import heartbeat_cards
        assert heartbeat_cards.is_card_key is is_card_key
        assert heartbeat_unreal.is_pci_card_key is is_pci_card_key


def _gpu(**extra):
    data = {'index': 1, 'name': 'Example Card 24G', 'total_mb': 24560, 'card_key': 'pci:0000:03:00.0'}
    data.update(extra)
    return data


class TestPilotGpuSerializerCardKey:

    @pytest.mark.parametrize('key', ['pci:0000:03:00.0', 'pci:ffffffff:ff:ff.7'])
    def test_valid_key_passes(self, key):
        serializer = PilotGpuSerializer(data=_gpu(card_key=key))
        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data['card_key'] == key

    def test_missing_key_fails(self):
        data = _gpu()
        del data['card_key']
        serializer = PilotGpuSerializer(data=data)
        assert serializer.is_valid() is False
        assert set(serializer.errors) == {'card_key'}

    @pytest.mark.parametrize('key', [
        None, 'apple-gpu', '', 'not a key', 'PCI:0000:03:00.0', 'pci:0000:03:00.8',
        'pci:000000000:03:00.0', 'pci:0000:03:00.0 ', 5, True, ['pci:0000:03:00.0'],
    ])
    def test_bad_key_fails(self, key):
        serializer = PilotGpuSerializer(data=_gpu(card_key=key))
        assert serializer.is_valid() is False
        assert set(serializer.errors) == {'card_key'}


class TestSourceEnum:

    def test_choices_are_the_sorted_tuple(self):
        choices = worker_serializers.GPU_MEMORY_SOURCE_CHOICES
        assert isinstance(choices, tuple)
        assert choices == SOURCES
        assert choices == tuple(sorted(choices))

    def test_enum_name_is_pinned_to_the_tuple(self):
        path = settings.SPECTACULAR_SETTINGS['ENUM_NAME_OVERRIDES']['WorkerGpuMemorySourceEnum']
        assert path == 'workers.serializers.workers.GPU_MEMORY_SOURCE_CHOICES'
        assert import_string(path) is worker_serializers.GPU_MEMORY_SOURCE_CHOICES

    def test_heartbeat_membership_set_matches_the_tuple(self):
        assert heartbeat_unreal.GPU_MEMORY_SOURCES == frozenset(SOURCES)

    def test_schema_source_field_uses_the_tuple_in_order(self):
        field = worker_serializers._GPU_MEMORY_SCHEMA.fields['source']
        assert list(field.choices) == list(SOURCES)
