# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for where the Cycles feature set rule (#244) is applied.

- The Blender strategy (Job), TiledJob and Animation serializers run it;
  the Unreal strategy keeps its own rule.
- The TiledJob and Animation create views send no ``cycles.feature_set``
  render setting to child jobs on Blender 5.0 and later.

The view tests call ``perform_create`` without its ``transaction.atomic``
wrapper and with ``Job`` / ``AnimationFrame`` mocked, so no database is used.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from rest_framework.exceptions import ValidationError

from workers.constants import CyclesFeatureSet, RenderEngine, RenderSettings
from workers.renderers import get_strategy
from workers.serializers import AnimationSerializer, TiledJobSerializer, validation_utils
from workers.views import animations as animation_views
from workers.views import tiled_jobs as tiled_job_views

EXP = CyclesFeatureSet.EXPERIMENTAL
SUP = CyclesFeatureSet.SUPPORTED
FEATURE_KEY = RenderSettings.CYCLES_FEATURE_SET
UNREAL_SETTINGS = {
    'unreal.map': '/Game/Maps/Main.Main',
    'unreal.sequence': '/Game/Seq/Shot.Shot',
    'unreal.preset': '/Game/Presets/P.P',
}


def _version(series):
    return SimpleNamespace(series=series) if series is not None else None


def _blender_asset(series):
    project = SimpleNamespace(renderer='BLENDER', blender_version=_version(series))
    return SimpleNamespace(project=project)


def _feature_set_errors(func, *args):
    with pytest.raises(ValidationError) as exc_info:
        func(*args)
    assert list(exc_info.value.detail) == ['cycles_feature_set']
    return [str(m) for m in exc_info.value.detail['cycles_feature_set']]


class TestBlenderStrategy:

    @pytest.fixture
    def blender(self):
        return get_strategy('BLENDER')

    def test_create_experimental_on_5x_refused(self, blender):
        asset = _blender_asset('5.0')
        messages = _feature_set_errors(
            blender.validate_job, {'asset': asset, 'cycles_feature_set': EXP}, asset, None,
        )
        assert 'this job uses Blender 5.0' in messages[0]

    def test_create_experimental_on_4x_passes(self, blender):
        asset = _blender_asset('4.5')
        attrs = {'asset': asset, 'cycles_feature_set': EXP}
        assert blender.validate_job(attrs, asset, None) is attrs

    def test_create_supported_on_5x_passes(self, blender):
        asset = _blender_asset('5.2')
        attrs = {'asset': asset, 'cycles_feature_set': SUP}
        assert blender.validate_job(attrs, asset, None) is attrs

    def test_update_version_to_5x_refused(self, blender):
        instance = SimpleNamespace(cycles_feature_set=EXP, blender_version=None, asset=_blender_asset('4.5'))
        _feature_set_errors(blender.validate_job, {'blender_version': _version('5.0')}, instance.asset, instance)

    def test_status_patch_on_invalid_pair_passes(self, blender):
        instance = SimpleNamespace(cycles_feature_set=EXP, blender_version=None, asset=_blender_asset('5.2'))
        attrs = {'status': 'DONE'}
        assert blender.validate_job(attrs, instance.asset, instance) is attrs

    def test_passes_attrs_and_instance_to_validator(self, blender, mocker):
        spy = mocker.patch.object(validation_utils, 'validate_cycles_feature_set')
        attrs, instance = {'name': 'J'}, SimpleNamespace()
        blender.validate_job(attrs, MagicMock(), instance)
        spy.assert_called_once_with(attrs, instance)

    def test_engine_build_error_comes_first(self, blender):
        asset = _blender_asset('5.0')
        with pytest.raises(ValidationError) as exc_info:
            blender.validate_job({'engine_build': '5.8.4-1', 'cycles_feature_set': EXP}, asset, None)
        assert exc_info.value.detail == {'engine_build': ["Not applicable to Blender jobs."]}


class TestUnrealStrategy:

    @pytest.fixture
    def unreal(self):
        return get_strategy('UNREAL')

    @staticmethod
    def _create_attrs(**extra):
        return {'start_frame': 1, 'end_frame': 1, 'output_file_pattern': 'out/f_####.png',
                'render_settings': dict(UNREAL_SETTINGS), **extra}

    @pytest.mark.parametrize('extra', [{}, {'cycles_feature_set': SUP}])
    def test_create_with_supported_or_omitted_passes(self, unreal, extra):
        attrs = self._create_attrs(**extra)
        assert unreal.validate_job(attrs, MagicMock(), None) is attrs

    def test_create_experimental_refused_by_unreal_rule(self, unreal):
        messages = _feature_set_errors(
            unreal.validate_job, self._create_attrs(cycles_feature_set=EXP), MagicMock(), None,
        )
        assert messages == ["cycles_feature_set is not applicable to Unreal jobs."]

    def test_update_experimental_refused_by_unreal_rule(self, unreal):
        instance = SimpleNamespace(name='shot')
        messages = _feature_set_errors(unreal.validate_job, {'cycles_feature_set': EXP}, MagicMock(), instance)
        assert messages == ["cycles_feature_set is not applicable to Unreal jobs."]

    def test_does_not_run_blender_version_rule(self, unreal, mocker):
        spy = mocker.patch.object(validation_utils, 'validate_cycles_feature_set')
        unreal.validate_job(self._create_attrs(cycles_feature_set=SUP), MagicMock(), None)
        spy.assert_not_called()


class TestParentSerializers:

    @pytest.mark.parametrize('serializer_class', [TiledJobSerializer, AnimationSerializer])
    def test_create_experimental_on_5x_refused(self, serializer_class):
        asset = _blender_asset('5.2')
        data = {'project': asset.project, 'asset': asset, 'cycles_feature_set': EXP}
        messages = _feature_set_errors(serializer_class().validate, data)
        assert 'this job uses Blender 5.2' in messages[0]

    @pytest.mark.parametrize('serializer_class', [TiledJobSerializer, AnimationSerializer])
    def test_update_uses_instance(self, serializer_class):
        instance = SimpleNamespace(cycles_feature_set=SUP, blender_version=None, asset=_blender_asset('5.0'))
        _feature_set_errors(serializer_class(instance=instance).validate, {'cycles_feature_set': EXP})

    @pytest.mark.parametrize('module,serializer_class', [
        ('workers.serializers.jobs', TiledJobSerializer),
        ('workers.serializers.animations', AnimationSerializer),
    ])
    def test_validator_gets_data_and_instance(self, mocker, module, serializer_class):
        stop = ValidationError('stop')
        spy = mocker.patch(f'{module}.validate_cycles_feature_set', side_effect=stop)
        instance = SimpleNamespace(asset=_blender_asset('4.5'))
        data = {'cycles_feature_set': SUP}
        with pytest.raises(ValidationError):
            serializer_class(instance=instance).validate(data)
        spy.assert_called_once_with(data, instance)


def _parent(series, engine=RenderEngine.CYCLES, feature_set=SUP, settings=None, override=None):
    """A saved TiledJob/Animation stand-in with the fields perform_create reads."""
    asset = _blender_asset(series)
    blender_version = _version(override)
    return SimpleNamespace(
        id=7, name='P', asset=asset, blender_version=blender_version,
        effective_blender_version=blender_version or asset.project.blender_version,
        render_engine=engine, render_device='GPU', cycles_feature_set=feature_set,
        render_settings=dict(settings or {}),
        # TiledJob
        tile_count_x=2, tile_count_y=1, final_resolution_x=100, final_resolution_y=50,
        # Animation
        tiling_config='NONE', start_frame=1, end_frame=2, frame_step=1,
        output_file_pattern='out/f_####.png', get_tile_counts=lambda: (2, 1),
    )


def _spawn(module, view_class, parent, mocker):
    """Run perform_create for ``parent``; return the kwargs of every child Job."""
    job_class = mocker.patch.object(module, 'Job')
    if hasattr(module, 'AnimationFrame'):
        mocker.patch.object(module, 'AnimationFrame').objects.create.return_value = SimpleNamespace(id=3)
    serializer = MagicMock()
    serializer.save.return_value = parent
    view_class.perform_create.__wrapped__(view_class(), serializer)
    job_class.objects.bulk_create.assert_called_once()
    children = [call.kwargs for call in job_class.call_args_list]
    assert children
    return children


VIEWS = [
    pytest.param(tiled_job_views, tiled_job_views.TiledJobViewSet, 'NONE', id='tiled-job'),
    pytest.param(animation_views, animation_views.AnimationViewSet, 'NONE', id='animation'),
    pytest.param(animation_views, animation_views.AnimationViewSet, '2x2', id='tiled-animation'),
]


@pytest.mark.parametrize('module,view_class,tiling', VIEWS)
class TestChildDispatch:

    @pytest.mark.parametrize('series', ['5.0', '5.2'])
    def test_no_key_on_5x(self, mocker, module, view_class, tiling, series):
        parent = _parent(series)
        parent.tiling_config = tiling
        for child in _spawn(module, view_class, parent, mocker):
            assert FEATURE_KEY not in child['render_settings']
            assert child['cycles_feature_set'] == SUP

    def test_user_key_removed_on_5x(self, mocker, module, view_class, tiling):
        parent = _parent('5.0', settings={FEATURE_KEY: 'EXPERIMENTAL', 'cycles.samples': 8})
        parent.tiling_config = tiling
        for child in _spawn(module, view_class, parent, mocker):
            assert FEATURE_KEY not in child['render_settings']
            assert child['render_settings']['cycles.samples'] == 8
        assert parent.render_settings[FEATURE_KEY] == 'EXPERIMENTAL'  # parent row not changed

    def test_5x_override_on_4x_project_has_no_key(self, mocker, module, view_class, tiling):
        parent = _parent('4.5', override='5.0')
        parent.tiling_config = tiling
        for child in _spawn(module, view_class, parent, mocker):
            assert FEATURE_KEY not in child['render_settings']

    @pytest.mark.parametrize('feature_set', [SUP, EXP])
    def test_key_set_on_4x_cycles(self, mocker, module, view_class, tiling, feature_set):
        parent = _parent('4.5', feature_set=feature_set)
        parent.tiling_config = tiling
        for child in _spawn(module, view_class, parent, mocker):
            assert child['render_settings'][FEATURE_KEY] == feature_set
            assert child['render_settings'][RenderSettings.RENDER_ENGINE] == RenderEngine.CYCLES

    def test_4x_override_on_5x_project_keeps_key(self, mocker, module, view_class, tiling):
        parent = _parent('5.2', feature_set=EXP, override='4.2')
        parent.tiling_config = tiling
        for child in _spawn(module, view_class, parent, mocker):
            assert child['render_settings'][FEATURE_KEY] == EXP

    def test_no_key_for_non_cycles_on_4x(self, mocker, module, view_class, tiling):
        parent = _parent('4.5', engine=RenderEngine.EEVEE)
        parent.tiling_config = tiling
        for child in _spawn(module, view_class, parent, mocker):
            assert FEATURE_KEY not in child['render_settings']

    def test_unknown_version_keeps_key(self, mocker, module, view_class, tiling):
        parent = _parent(None)
        parent.tiling_config = tiling
        for child in _spawn(module, view_class, parent, mocker):
            assert child['render_settings'][FEATURE_KEY] == SUP
