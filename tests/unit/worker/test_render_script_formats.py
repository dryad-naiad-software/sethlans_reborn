# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Output-format settings in dependency order, and the Blender 5.x media type (#243).

Which ``color_depth`` Blender accepts depends on the file format set at that
moment, and PostgreSQL/MySQL return JSON keys re-sorted, so the depth key can
arrive before the format key. On 5.x a multilayer EXR also needs
``media_type = 'MULTI_LAYER_IMAGE'`` first (and a plain format needs
``IMAGE`` when the .blend was saved multilayer).

Regression (fail on the generator before #243, which applied keys in arrival
order and never touched ``media_type``): the depth-before-format EXR 32 cases
(depth 32 is rejected while the format is still PNG), the PostgreSQL-order
case, the 5.x multilayer case, the "saved multilayer, job wants PNG" case,
the arrival-order check, and the depth-first wrong combination (depth 16 was
accepted while the format was PNG, then setting JPEG moved it to 8 without
an error). The PNG 16 and EXR 16 depth-first cases pass on both, since 16 is
valid for PNG.
"""
import pytest

from sethlans_worker_agent.format_script import order_settings
from sethlans_worker_agent.render_script import generate_render_config_script
from tests.unit.worker._fake_bpy import FakePrefs, install_fake_bpy, make_scene, run_script
from tests.unit.worker._fake_image_settings import FakeImageSettings

FMT, DEPTH, QUALITY = ('render.image_settings.file_format', 'render.image_settings.color_depth',
                       'render.image_settings.quality')
FLAVOURS = {'4.x': lambda: FakeImageSettings(), '5.x': lambda: FakeImageSettings(media_type='IMAGE')}


def _run(mocker, monkeypatch, settings, image_settings):
    mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=[])
    install_fake_bpy(monkeypatch, FakePrefs([]), scenes=[make_scene(image_settings=image_settings)])
    script = generate_render_config_script(243, 'CYCLES', 'CPU', settings)
    run_script(script)
    return script


def _db_order(settings):
    """Key order PostgreSQL jsonb / MySQL JSON return: shorter keys first, then bytewise."""
    return dict(sorted(settings.items(), key=lambda kv: (len(kv[0]), kv[0])))


class TestOrderSettings:

    def test_format_group_takes_the_first_image_key_position(self):
        settings = {'cycles.samples': 1, DEPTH: '16', 'render.resolution_x': 800,
                    FMT: 'PNG', QUALITY: 90, 'render.use_border': True}

        assert [k for k, _ in order_settings(settings)] == [
            'cycles.samples', FMT, DEPTH, QUALITY, 'render.resolution_x', 'render.use_border',
        ]

    def test_without_image_keys_order_is_unchanged(self):
        settings = {'b': 1, 'a': 2, 'render.resolution_x': 3}
        assert order_settings(settings) == list(settings.items())


class TestDepthAndFormat:

    @pytest.mark.parametrize('flavour', sorted(FLAVOURS))
    @pytest.mark.parametrize('fmt, depth', [('PNG', '16'), ('OPEN_EXR', '32'), ('OPEN_EXR', '16')])
    @pytest.mark.parametrize('depth_first', [True, False], ids=['depth-format', 'format-depth'])
    def test_both_key_orders_end_with_both_applied(self, mocker, monkeypatch, flavour, fmt, depth, depth_first):
        settings = {DEPTH: depth, FMT: fmt} if depth_first else {FMT: fmt, DEPTH: depth}
        image = FLAVOURS[flavour]()

        _run(mocker, monkeypatch, settings, image)

        assert (image.file_format, image.color_depth) == (fmt, depth)

    def test_exr_32_in_database_key_order(self, mocker, monkeypatch):
        settings = _db_order({'cycles.samples': 64, 'render.resolution_x': 1920, 'render.resolution_y': 1080,
                              FMT: 'OPEN_EXR', DEPTH: '32'})
        assert list(settings).index(DEPTH) < list(settings).index(FMT)  # the case that used to fail
        image = FakeImageSettings(media_type='IMAGE')

        _run(mocker, monkeypatch, settings, image)

        assert (image.file_format, image.color_depth) == ('OPEN_EXR', '32')

    @pytest.mark.parametrize('settings', [{FMT: 'JPEG', DEPTH: '16'}, {DEPTH: '16', FMT: 'JPEG'}],
                             ids=['format-depth', 'depth-format'])
    def test_wrong_combination_still_raises(self, mocker, monkeypatch, settings):
        with pytest.raises(TypeError, match='enum "16" not found'):
            _run(mocker, monkeypatch, settings, FakeImageSettings(media_type='IMAGE'))

    def test_jpeg_quality_after_format(self, mocker, monkeypatch):
        image = FakeImageSettings(media_type='IMAGE')
        _run(mocker, monkeypatch, {QUALITY: 75, FMT: 'JPEG'}, image)
        assert (image.file_format, image.quality) == ('JPEG', 75)


class TestMediaType:

    def test_multilayer_on_5x_switches_media_type_first(self, mocker, monkeypatch, capsys):
        image = FakeImageSettings(media_type='IMAGE')

        _run(mocker, monkeypatch, {DEPTH: '32', FMT: 'OPEN_EXR_MULTILAYER'}, image)

        assert (image.media_type, image.file_format, image.color_depth) == (
            'MULTI_LAYER_IMAGE', 'OPEN_EXR_MULTILAYER', '32')
        assert image.history[:2] == [('media_type', 'MULTI_LAYER_IMAGE'), ('file_format', 'OPEN_EXR_MULTILAYER')]
        assert 'Switching media type to MULTI_LAYER_IMAGE for file format OPEN_EXR_MULTILAYER' in capsys.readouterr().out

    def test_multilayer_on_4x_sets_the_format_directly(self, mocker, monkeypatch, capsys):
        image = FakeImageSettings()
        assert not hasattr(image, 'media_type')

        _run(mocker, monkeypatch, {FMT: 'OPEN_EXR_MULTILAYER', DEPTH: '32'}, image)

        assert (image.file_format, image.color_depth) == ('OPEN_EXR_MULTILAYER', '32')
        assert 'Switching media type' not in capsys.readouterr().out

    def test_plain_format_on_a_file_saved_multilayer(self, mocker, monkeypatch):
        image = FakeImageSettings(media_type='MULTI_LAYER_IMAGE', file_format='OPEN_EXR_MULTILAYER')

        _run(mocker, monkeypatch, {DEPTH: '16', FMT: 'PNG'}, image)

        assert (image.media_type, image.file_format, image.color_depth) == ('IMAGE', 'PNG', '16')

    def test_media_type_left_alone_when_already_right(self, mocker, monkeypatch):
        image = FakeImageSettings(media_type='IMAGE')

        _run(mocker, monkeypatch, {FMT: 'PNG'}, image)

        assert ('media_type', 'IMAGE') not in image.history

    def test_unknown_format_gets_no_media_switch_and_still_raises(self, mocker, monkeypatch):
        mocker.patch('sethlans_worker_agent.system_monitor.detect_gpu_devices', return_value=[])
        script = generate_render_config_script(243, 'CYCLES', 'CPU', {FMT: 'GIF'})
        assert 'media_type' not in script

        with pytest.raises(TypeError, match='enum "GIF" not found'):
            _run(mocker, monkeypatch, {FMT: 'GIF'}, FakeImageSettings(media_type='IMAGE'))


class TestImageSettingsWithoutFormat:
    """Image settings but no ``file_format``: the ``-F`` format is set first.

    ``-F`` (format_utils.get_format_and_extension) is applied only after the
    script, so before this the depth was checked against the .blend file's
    own format: the JPEG and multilayer cases here raise without it.
    """

    def test_depth_only_on_a_file_saved_as_jpeg(self, mocker, monkeypatch):
        image = FakeImageSettings(file_format='JPEG')

        _run(mocker, monkeypatch, {'cycles.samples': 8, DEPTH: '16'}, image)

        assert (image.file_format, image.color_depth) == ('PNG', '16')

    def test_depth_only_on_5x_file_saved_multilayer(self, mocker, monkeypatch):
        image = FakeImageSettings(media_type='MULTI_LAYER_IMAGE', file_format='OPEN_EXR_MULTILAYER')

        _run(mocker, monkeypatch, {DEPTH: '16'}, image)

        assert (image.media_type, image.file_format, image.color_depth) == ('IMAGE', 'PNG', '16')

    def test_format_comes_from_format_utils(self, mocker, monkeypatch):
        mocker.patch('sethlans_worker_agent.format_script.get_format_and_extension',
                     return_value=('TIFF', '.tif'))
        image = FakeImageSettings(file_format='JPEG')

        _run(mocker, monkeypatch, {DEPTH: '16'}, image)

        assert (image.file_format, image.color_depth) == ('TIFF', '16')

    def test_no_image_settings_adds_no_format_line(self, mocker, monkeypatch):
        script = _run(mocker, monkeypatch, {'cycles.samples': 8}, FakeImageSettings(file_format='JPEG'))
        assert 'file_format' not in script

    def test_explicit_format_is_not_duplicated(self, mocker, monkeypatch):
        script = _run(mocker, monkeypatch, {DEPTH: '16', FMT: 'TIFF'}, FakeImageSettings())
        assert script.count('scene.render.image_settings.file_format = ') == 1
        assert "scene.render.image_settings.file_format = 'TIFF'" in script


class TestOtherSettingsKeepTheirOrder:

    def test_non_format_lines_in_arrival_order(self, mocker, monkeypatch):
        settings = {'render.resolution_x': 800, DEPTH: '16', 'cycles.samples': 8,
                    'render.use_border': True, FMT: 'PNG', 'render.border_max_x': 0.5}

        script = _run(mocker, monkeypatch, settings, FakeImageSettings(media_type='IMAGE'))

        lines = [ln.strip() for ln in script.splitlines() if ln.startswith('    scene.')]
        assert lines == [
            'scene.render.resolution_x = 800',
            "scene.render.image_settings.file_format = 'PNG'",
            "scene.render.image_settings.color_depth = '16'",
            'scene.cycles.samples = 8',
            'scene.render.use_border = True',
            'scene.render.border_max_x = 0.5',
        ]


def test_media_type_table_covers_every_offered_format():
    """The media type table and the worker's format list must not drift apart."""
    from sethlans_worker_agent.format_script import FORMAT_MEDIA_TYPES
    from sethlans_worker_agent.format_utils import FORMAT_EXTENSIONS

    assert set(FORMAT_MEDIA_TYPES) == set(FORMAT_EXTENSIONS)
