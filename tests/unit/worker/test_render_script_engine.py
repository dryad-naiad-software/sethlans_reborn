# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Render engine names per Blender version (#242).

The product sends ``CYCLES``, ``BLENDER_EEVEE_NEXT`` and ``WORKBENCH``.
Blender accepts ``BLENDER_EEVEE`` on 4.1 and 5.x, ``BLENDER_EEVEE_NEXT``
on 4.2 to 4.5, and ``BLENDER_WORKBENCH`` (never ``WORKBENCH``) everywhere.
The generated script sets the engine by family through a fixed helper.

Regression: the old generator assigned the product name directly, both in
the engine line and for the ``render.engine`` key that tile jobs and
animation frames carry, so the 5.x Eevee cases and every Workbench case
below raised ``TypeError`` and the settings after it were never applied.
These tests fail on that generator.
"""
import pytest

from sethlans_worker_agent import blender_thumbnail
from sethlans_worker_agent.engine_script import ENGINE_HELPER
from sethlans_worker_agent.render_script import generate_render_config_script
from tests.unit.worker._fake_bpy import (
    ENGINES_4_1, ENGINES_4_2_TO_4_5, ENGINES_5_X,
    FakePrefs, install_fake_bpy, make_scene, run_script,
)

VERSIONS = {'4.1': ENGINES_4_1, '4.2-4.5': ENGINES_4_2_TO_4_5, '5.x': ENGINES_5_X}
EXPECTED = [
    ('4.1', 'CYCLES', 'CYCLES'),
    ('4.1', 'BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'),
    ('4.1', 'WORKBENCH', 'BLENDER_WORKBENCH'),
    ('4.2-4.5', 'CYCLES', 'CYCLES'),
    ('4.2-4.5', 'BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE_NEXT'),
    ('4.2-4.5', 'WORKBENCH', 'BLENDER_WORKBENCH'),
    ('5.x', 'CYCLES', 'CYCLES'),
    ('5.x', 'BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'),
    ('5.x', 'WORKBENCH', 'BLENDER_WORKBENCH'),
]


def _tile_settings(engine):
    """A tile job's settings, ordered as manager/workers/views/tiled_jobs.py builds them."""
    return {
        'render.image_settings.file_format': 'PNG',
        'render.engine': engine,
        'render.resolution_x': 800, 'render.resolution_y': 600,
        'render.resolution_percentage': 100,
        'render.use_border': True, 'render.use_crop_to_border': True,
        'render.border_min_x': 0.0, 'render.border_max_x': 0.5,
        'render.border_min_y': 0.0, 'render.border_max_y': 0.5,
    }


def _generate(mocker, engine, settings):
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=[])
    return generate_render_config_script(242, engine, 'CPU', settings)


def _scenes(version, count=1):
    return [make_scene(f'Scene{i}', engines=VERSIONS[version]) for i in range(count)]


class TestEngineByVersion:

    @pytest.mark.parametrize('version, engine, identifier', EXPECTED)
    def test_tile_job_gets_the_accepted_identifier_in_every_scene(
        self, mocker, monkeypatch, capsys, version, engine, identifier,
    ):
        scenes = _scenes(version, count=2)
        install_fake_bpy(monkeypatch, FakePrefs([]), scenes=scenes)

        run_script(_generate(mocker, engine, _tile_settings(engine)))

        for scene in scenes:
            assert scene.render.engine == identifier
            assert (scene.render.resolution_x, scene.render.resolution_y) == (800, 600)
            assert scene.render.use_border is True
            assert scene.render.border_max_y == 0.5
        # Top line (context scene) plus one per scene of the settings loop.
        assert capsys.readouterr().out.count(f'Render engine {engine}: set {identifier}') == 3

    @pytest.mark.parametrize('version, engine, identifier', EXPECTED)
    def test_single_job_without_engine_key(self, mocker, monkeypatch, version, engine, identifier):
        scenes = _scenes(version)
        install_fake_bpy(monkeypatch, FakePrefs([]), scenes=scenes)

        run_script(_generate(mocker, engine, {'render.resolution_x': 800}))

        assert scenes[0].render.engine == identifier
        assert scenes[0].render.resolution_x == 800

    @pytest.mark.parametrize('engine, identifier', [
        ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT'), ('BLENDER_WORKBENCH', 'BLENDER_WORKBENCH'),
    ])
    def test_blender_names_are_accepted_as_input(self, mocker, monkeypatch, engine, identifier):
        scenes = _scenes('4.2-4.5')
        install_fake_bpy(monkeypatch, FakePrefs([]), scenes=scenes)

        run_script(_generate(mocker, engine, _tile_settings(engine)))

        assert scenes[0].render.engine == identifier


class TestEngineErrors:

    def test_no_identifier_accepted_raises_a_clear_error(self, mocker, monkeypatch):
        scene = make_scene(engines={'CYCLES'})
        install_fake_bpy(monkeypatch, FakePrefs([]), scenes=[scene])

        with pytest.raises(ValueError) as excinfo:
            run_script(_generate(mocker, 'WORKBENCH', {}))

        assert str(excinfo.value) == (
            "Render engine 'WORKBENCH': this Blender accepts none of ['BLENDER_WORKBENCH']"
        )
        assert scene.render.engine is None

    def test_unknown_engine_key_value_still_raises(self, mocker, monkeypatch):
        settings = _tile_settings('CYCLES')
        settings['render.engine'] = 'MY_ENGINE'
        install_fake_bpy(monkeypatch, FakePrefs([]), scenes=_scenes('5.x'))
        script = _generate(mocker, 'CYCLES', settings)

        assert "    scene.render.engine = 'MY_ENGINE'" in script.splitlines()
        with pytest.raises(TypeError, match='enum "MY_ENGINE" not found'):
            run_script(script)

    def test_helper_lets_other_type_errors_through(self):
        class Render:
            @property
            def engine(self):
                return None

            @engine.setter
            def engine(self, value):
                raise TypeError('bpy_struct: item.attr = val: expected a string type')

        namespace = run_script(ENGINE_HELPER)
        with pytest.raises(TypeError, match='expected a string type'):
            namespace['_sethlans_set_engine'](Render(), 'CYCLES', ('CYCLES',))


class TestThumbnailScript:

    @pytest.mark.parametrize('version, engine, identifier', [
        ('4.2-4.5', 'BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE_NEXT'),
        ('5.x', 'BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'),
        ('5.x', 'WORKBENCH', 'BLENDER_WORKBENCH'),
    ])
    def test_thumbnail_pass_sets_the_accepted_identifier(
        self, mocker, monkeypatch, tmp_path, version, engine, identifier,
    ):
        mocker.patch('sethlans_worker_agent.config.WORKER_TEMP_DIR', str(tmp_path))
        mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=[])
        process = mocker.MagicMock()
        process.wait.return_value = 1  # no thumbnail file: only the script matters here
        process.stdout.readline.return_value = ''
        process.stderr.readline.return_value = ''
        mocker.patch('sethlans_worker_agent.blender_thumbnail.subprocess.Popen', return_value=process)
        spy = mocker.spy(blender_thumbnail, 'generate_render_config_script')
        job = {'id': 242, 'render_engine': engine, 'render_device': 'CPU',
               'render_settings': {'render.resolution_x': 1600, 'render.resolution_y': 900}}

        blender_thumbnail.render_exr_hdr_thumbnail(
            job, 'blender', str(tmp_path / 'scene.blend'), 1, str(tmp_path / 'out_####.exr'), None,
        )

        scenes = _scenes(version)
        install_fake_bpy(monkeypatch, FakePrefs([]), scenes=scenes)
        run_script(spy.spy_return)
        assert scenes[0].render.engine == identifier
        assert (scenes[0].render.resolution_x, scenes[0].render.resolution_y) == (480, 270)
