# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``cycles.feature_set`` on Blender versions without it (#241).

The manager adds ``cycles.feature_set`` to every Cycles tile job and
animation frame, before the resolution and border keys. Blender 5.x's
Cycles settings have no such property, so a plain assignment raised and
the rest of the settings script (resolution, tile border) never ran. The
generated script now applies that one key only when the property exists;
every other key is still assigned directly and still raises when unknown.

Each test executes the real generated script against the fake ``bpy``,
whose scene objects raise ``AttributeError`` on unknown attributes.
"""
import pytest

from sethlans_worker_agent.render_script import generate_render_config_script
from tests.unit.worker._fake_bpy import FakePrefs, install_fake_bpy, make_scene, run_script

SKIP_LINE = 'Skipping cycles.feature_set for scene'


def _tile_job_settings(feature_set='SUPPORTED', extra=None):
    """Settings in the order manager/workers/views/tiled_jobs.py builds them."""
    base = {'cycles.samples': 64, 'render.image_settings.file_format': 'PNG'}  # the user's settings
    base.update(extra or {})
    base['render.engine'] = 'CYCLES'
    base['cycles.feature_set'] = feature_set
    tile = base.copy()
    tile.update({
        'render.resolution_x': 800,
        'render.resolution_y': 600,
        'render.resolution_percentage': 100,
        'render.use_border': True,
        'render.use_crop_to_border': True,
        'render.border_min_x': 0.0,
        'render.border_max_x': 0.5,
        'render.border_min_y': 0.0,
        'render.border_max_y': 0.5,
    })
    return tile


def _run(mocker, monkeypatch, settings, scenes):
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=[])
    mocker.patch('sethlans_worker_agent.config.FORCE_GPU_INDEX', None)
    install_fake_bpy(monkeypatch, FakePrefs([]), scenes=scenes)
    script = generate_render_config_script(241, 'CYCLES', 'CPU', settings)
    run_script(script)
    return script


def _assert_tile_applied(scene):
    render = scene.render
    assert (render.resolution_x, render.resolution_y) == (800, 600)
    assert render.resolution_percentage == 100
    assert render.use_border is True
    assert render.use_crop_to_border is True
    assert (render.border_min_x, render.border_max_x) == (0.0, 0.5)
    assert (render.border_min_y, render.border_max_y) == (0.0, 0.5)
    assert scene.cycles.samples == 64
    assert render.engine == 'CYCLES'


class TestFeatureSetMissing:
    """Blender 5.x: the Cycles settings have no ``feature_set``."""

    def test_rest_of_the_tile_settings_still_apply(self, mocker, monkeypatch, capsys):
        scene = make_scene(cycles_feature_set=False)

        _run(mocker, monkeypatch, _tile_job_settings(), [scene])

        _assert_tile_applied(scene)
        assert not hasattr(scene.cycles, 'feature_set')
        skipped = [line for line in capsys.readouterr().out.splitlines() if line.startswith(SKIP_LINE)]
        assert skipped == ["Skipping cycles.feature_set for scene 'Scene': this Blender version has no such setting."]

    def test_every_scene_is_covered(self, mocker, monkeypatch, capsys):
        scenes = [make_scene('Main'), make_scene('Alt')]

        _run(mocker, monkeypatch, _tile_job_settings(), scenes)

        for scene in scenes:
            _assert_tile_applied(scene)
        out = capsys.readouterr().out
        assert out.count(SKIP_LINE) == 2
        assert "'Alt'" in out


class TestFeatureSetPresent:
    """Blender 4.x: the property exists and is set to the job's value."""

    @pytest.mark.parametrize('value', ['SUPPORTED', 'EXPERIMENTAL'])
    def test_value_is_set_and_the_rest_applies(self, mocker, monkeypatch, capsys, value):
        scene = make_scene(cycles_feature_set=True)

        _run(mocker, monkeypatch, _tile_job_settings(feature_set=value), [scene])

        assert scene.cycles.feature_set == value
        _assert_tile_applied(scene)
        assert SKIP_LINE not in capsys.readouterr().out


class TestOtherKeysStayStrict:

    @pytest.mark.parametrize('feature_set_exists', [False, True])
    def test_unknown_user_key_still_raises(self, mocker, monkeypatch, feature_set_exists):
        settings = _tile_job_settings(extra={'cycles.no_such_setting': 1})
        scene = make_scene(cycles_feature_set=feature_set_exists)

        with pytest.raises(AttributeError, match="no attribute 'no_such_setting'"):
            _run(mocker, monkeypatch, settings, [scene])

    @pytest.mark.parametrize('feature_set_exists', [False, True])
    def test_unknown_key_after_the_guard_still_raises(self, mocker, monkeypatch, feature_set_exists):
        """A key emitted after the guarded block must not be swallowed by it."""
        settings = _tile_job_settings()
        settings['render.no_such_setting'] = 1  # last key, after feature_set and the border keys
        scene = make_scene(cycles_feature_set=feature_set_exists)

        with pytest.raises(AttributeError, match="no attribute 'no_such_setting'"):
            _run(mocker, monkeypatch, settings, [scene])

    def test_only_the_feature_set_line_is_guarded(self, mocker, monkeypatch):
        script = _run(mocker, monkeypatch, _tile_job_settings(), [make_scene()])

        lines = script.splitlines()
        # The only other guard is the 5.x media type before the file format (#243).
        assert script.count('hasattr(') == 2
        assert script.count("hasattr(scene.render.image_settings, 'media_type')") == 1
        assert "    if hasattr(scene.cycles, 'feature_set'):" in lines
        assert "        scene.cycles.feature_set = 'SUPPORTED'" in lines
        assert '    scene.render.resolution_x = 800' in lines
