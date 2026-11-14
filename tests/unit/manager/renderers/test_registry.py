# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the renderer registry (spec FR-MR-7/8, FR-T-4).
"""

import ast
import pathlib

import pytest

from workers.constants import AssetSourceType, Renderer
from workers.renderers import (
    RendererStrategy, UnsupportedRendererError, get_strategy, strategy_for_source_type,
)
from workers.renderers.blender import BlenderStrategy
from workers.renderers.unreal import UnrealStrategy

RENDERERS_DIR = pathlib.Path(__file__).resolve().parents[4] / 'manager' / 'workers' / 'renderers'
MANAGER_APP = RENDERERS_DIR.parent


class TestLookup:

    @pytest.mark.parametrize('value, cls', [
        (Renderer.BLENDER, BlenderStrategy), ('BLENDER', BlenderStrategy),
        (Renderer.UNREAL, UnrealStrategy), ('UNREAL', UnrealStrategy),
    ])
    def test_get_strategy(self, value, cls):
        assert isinstance(get_strategy(value), cls)

    @pytest.mark.parametrize('value, cls', [
        (AssetSourceType.BLEND_FILE, BlenderStrategy),
        ('shared_project', UnrealStrategy),
    ])
    def test_strategy_for_source_type(self, value, cls):
        assert isinstance(strategy_for_source_type(value), cls)

    @pytest.mark.parametrize('value', ['blender', 'CYCLES', '', None, ['BLENDER']])
    def test_unknown_renderer_raises_domain_error(self, value):
        with pytest.raises(UnsupportedRendererError):
            get_strategy(value)

    @pytest.mark.parametrize('value', ['blend_archive', 'BLEND_FILE', '', None])
    def test_unknown_source_type_raises_domain_error(self, value):
        with pytest.raises(UnsupportedRendererError):
            strategy_for_source_type(value)

    def test_domain_error_is_a_key_error(self):
        assert issubclass(UnsupportedRendererError, KeyError)

    def test_every_renderer_and_source_type_is_registered(self):
        assert {get_strategy(r).renderer for r in Renderer.values} == set(Renderer.values)
        assert {strategy_for_source_type(s).source_type for s in AssetSourceType.values} == set(
            AssetSourceType.values)

    def test_lookups_return_singletons(self):
        assert get_strategy('BLENDER') is strategy_for_source_type('blend_file')
        assert get_strategy('UNREAL') is strategy_for_source_type('shared_project')


class TestInterface:

    def test_strategies_implement_every_abstract_method(self):
        for strategy in (get_strategy('BLENDER'), get_strategy('UNREAL')):
            assert isinstance(strategy, RendererStrategy)
            assert not getattr(type(strategy), '__abstractmethods__', set())

    def test_interface_methods_match_the_spec(self):
        assert RendererStrategy.__abstractmethods__ == {
            'validate_project', 'prepare_project_create', 'validate_asset',
            'validate_job', 'prepare_job_create', 'poll_q', 'claim_rejection',
            'routing_note', 'describe_asset', 'supports', 'idempotent_reclaim',
            'worker_patch_allowed_fields', 'requeue_on_worker_restart',
            'supports_release',
        }

    @pytest.mark.parametrize('module', ['__init__.py', 'base.py'])
    def test_registry_and_base_import_no_drf(self, module):
        tree = ast.parse((RENDERERS_DIR / module).read_text(encoding='utf-8'))
        imported = [
            node.module or '' for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        ] + [
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        ]
        assert not [name for name in imported if name.startswith('rest_framework')]


class TestNoRendererBranching:
    """AC-4: dispatch goes only through the registry."""

    @pytest.mark.parametrize('package', ['serializers', 'views'])
    def test_no_renderer_equality_in_serializers_or_views(self, package):
        offenders = []
        for path in (MANAGER_APP / package).rglob('*.py'):
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                if 'renderer ==' in line or '== Renderer.' in line:
                    offenders.append(f'{path.name}:{number}: {line.strip()}')
        assert offenders == []
