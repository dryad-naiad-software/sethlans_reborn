# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: ``cycles.feature_set`` in the child jobs' render settings (#244).

Tiled jobs and animations hand ``cycles.feature_set`` to workers inside
each child Job's ``render_settings``. Blender 5.0 and later has no such
setting, so for those versions no child carries the key, even when the
request put it into ``render_settings`` by hand. On 4.x with Cycles every
child carries the parent's value; on 4.x with another engine none does.
"""

import pytest

from workers.constants import RenderSettings
from workers.models import Job

from .cycles_feature_set_helpers import (
    FEATURE_SET_KEY, KINDS, blend_asset, created_pk, post, version,
)

ENGINE_KEY = RenderSettings.RENDER_ENGINE
FORMAT_KEY = RenderSettings.IMAGE_FILE_FORMAT

# shape -> (kind, extra create fields, expected child count)
SHAPES = {
    'tiled_job': ('tiled_job', {}, 4),
    'animation': ('animation', {}, 2),
    'tiled_animation': ('animation', {'tiling_config': '2x2'}, 8),
}
ALL_SHAPES = list(SHAPES)


@pytest.fixture
def asset52(db):
    """An asset whose project renders with Blender 5.2."""
    return blend_asset('5.2', 'Asset52')


def _settings(**extra):
    settings = {
        FORMAT_KEY: 'PNG', RenderSettings.RESOLUTION_X: 640, RenderSettings.RESOLUTION_Y: 360,
    }
    settings.update(extra)
    return settings


def _children(client, shape, asset, **fields):
    """Create the parent through the API and return its child jobs."""
    kind, shape_fields, count = SHAPES[shape]
    pk = created_pk(post(client, kind, asset, **shape_fields, **fields))
    parent_field = 'tiled_job' if kind == 'tiled_job' else 'animation'
    children = list(Job.objects.filter(**{parent_field: pk}))
    assert len(children) == count
    assert KINDS[kind][2].objects.filter(pk=pk).exists()
    return children


def _assert_no_key(children, engine):
    for child in children:
        assert FEATURE_SET_KEY not in child.render_settings, child.render_settings
        assert child.render_settings[ENGINE_KEY] == engine
        assert child.render_settings[FORMAT_KEY] == 'PNG'
        assert child.cycles_feature_set == 'SUPPORTED'


def _assert_key(children, value):
    for child in children:
        assert child.render_settings[FEATURE_SET_KEY] == value, child.render_settings
        assert child.render_settings[ENGINE_KEY] == 'CYCLES'
        assert child.cycles_feature_set == value


@pytest.mark.django_db
class TestBlender5Children:

    @pytest.mark.parametrize('shape', ALL_SHAPES)
    @pytest.mark.parametrize('series', ['5.0', '5.2'])
    def test_cycles_job_version_no_key(self, admin_client, asset, shape, series):
        children = _children(admin_client, shape, asset, render_engine='CYCLES',
                             blender_version=version(series).pk, render_settings=_settings())
        _assert_no_key(children, 'CYCLES')

    @pytest.mark.parametrize('shape', ALL_SHAPES)
    def test_project_version_without_job_version_no_key(self, admin_client, asset52, shape):
        children = _children(admin_client, shape, asset52, render_engine='CYCLES',
                             render_settings=_settings())
        _assert_no_key(children, 'CYCLES')

    @pytest.mark.parametrize('shape', ALL_SHAPES)
    @pytest.mark.parametrize('manual', ['EXPERIMENTAL', 'SUPPORTED'])
    def test_hand_written_key_removed(self, admin_client, asset52, shape, manual):
        children = _children(admin_client, shape, asset52, render_engine='CYCLES',
                             cycles_feature_set='SUPPORTED',
                             render_settings=_settings(**{FEATURE_SET_KEY: manual}))
        _assert_no_key(children, 'CYCLES')

    @pytest.mark.parametrize('shape', ALL_SHAPES)
    def test_hand_written_key_removed_for_eevee(self, admin_client, asset52, shape):
        children = _children(admin_client, shape, asset52, render_engine='BLENDER_EEVEE_NEXT',
                             render_settings=_settings(**{FEATURE_SET_KEY: 'EXPERIMENTAL'}))
        _assert_no_key(children, 'BLENDER_EEVEE_NEXT')


@pytest.mark.django_db
class TestBlender4Children:

    @pytest.mark.parametrize('shape', ALL_SHAPES)
    @pytest.mark.parametrize('feature_set', ['SUPPORTED', 'EXPERIMENTAL'])
    def test_cycles_carries_parent_value(self, admin_client, asset, shape, feature_set):
        children = _children(admin_client, shape, asset, render_engine='CYCLES',
                             cycles_feature_set=feature_set, render_settings=_settings())
        _assert_key(children, feature_set)

    @pytest.mark.parametrize('shape', ALL_SHAPES)
    def test_parent_value_wins_over_hand_written_key(self, admin_client, asset, shape):
        children = _children(admin_client, shape, asset, render_engine='CYCLES',
                             cycles_feature_set='SUPPORTED',
                             render_settings=_settings(**{FEATURE_SET_KEY: 'EXPERIMENTAL'}))
        _assert_key(children, 'SUPPORTED')

    @pytest.mark.parametrize('shape', ALL_SHAPES)
    def test_4x_job_version_on_5x_project_carries_experimental(self, admin_client, asset52, shape):
        children = _children(admin_client, shape, asset52, render_engine='CYCLES',
                             cycles_feature_set='EXPERIMENTAL',
                             blender_version=version('4.5').pk, render_settings=_settings())
        _assert_key(children, 'EXPERIMENTAL')

    @pytest.mark.parametrize('shape', ALL_SHAPES)
    @pytest.mark.parametrize('engine', ['BLENDER_EEVEE_NEXT', 'WORKBENCH'])
    def test_other_engine_no_key(self, admin_client, asset, shape, engine):
        children = _children(admin_client, shape, asset, render_engine=engine,
                             render_settings=_settings())
        _assert_no_key(children, engine)

    @pytest.mark.parametrize('shape', ALL_SHAPES)
    def test_default_engine_is_cycles_with_key(self, admin_client, asset, shape):
        # No engine, feature set or render settings sent: the model defaults apply.
        children = _children(admin_client, shape, asset)
        _assert_key(children, 'SUPPORTED')
