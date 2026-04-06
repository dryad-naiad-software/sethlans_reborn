# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the blender_thumbnail module.

Tests thumbnail render_settings construction, resolution scaling,
output pattern generation, and graceful failure handling.
"""
import os

from sethlans_worker_agent.blender_thumbnail import (
    _round_even,
    _build_thumbnail_settings,
    _build_thumbnail_output_pattern,
    render_exr_hdr_thumbnail,
)


class TestRoundEven:

    def test_even_stays_even(self):
        assert _round_even(480) == 480

    def test_odd_rounds_to_even(self):
        assert _round_even(479) == 480

    def test_float_rounds(self):
        assert _round_even(479.5) == 480

    def test_small_value(self):
        assert _round_even(3) == 4


class TestBuildThumbnailSettings:

    def test_default_resolution_scaling(self):
        """1920x1080 should produce 480x270 thumbnail."""
        settings = {
            'render.resolution_x': 1920,
            'render.resolution_y': 1080,
        }
        result = _build_thumbnail_settings(settings, 'CYCLES')
        assert result['render.resolution_x'] == 480
        assert result['render.resolution_y'] == 270
        assert result['render.resolution_percentage'] == 100
        assert result['render.image_settings.file_format'] == 'PNG'
        assert result['render.image_settings.color_depth'] == '8'
        assert result['cycles.samples'] == 32

    def test_small_resolution_uses_floor(self):
        """200x100 should be clamped to 256px minimum width."""
        settings = {
            'render.resolution_x': 200,
            'render.resolution_y': 100,
        }
        result = _build_thumbnail_settings(settings, 'CYCLES')
        assert result['render.resolution_x'] == 256
        # 256 * 100/200 = 128
        assert result['render.resolution_y'] == 128

    def test_medium_resolution_passthrough(self):
        """300x300 is between 256 and 480, so stays at 300."""
        settings = {
            'render.resolution_x': 300,
            'render.resolution_y': 300,
        }
        result = _build_thumbnail_settings(settings, 'CYCLES')
        assert result['render.resolution_x'] == 300
        assert result['render.resolution_y'] == 300

    def test_large_resolution_capped(self):
        """3840x2160 should be capped to 480px width."""
        settings = {
            'render.resolution_x': 3840,
            'render.resolution_y': 2160,
        }
        result = _build_thumbnail_settings(settings, 'CYCLES')
        assert result['render.resolution_x'] == 480
        assert result['render.resolution_y'] == 270

    def test_eevee_no_samples_override(self):
        """EEVEE engine should not include cycles.samples."""
        settings = {
            'render.resolution_x': 1920,
            'render.resolution_y': 1080,
        }
        result = _build_thumbnail_settings(settings, 'BLENDER_EEVEE_NEXT')
        assert 'cycles.samples' not in result

    def test_does_not_inherit_primary_settings(self):
        """Thumbnail settings must not carry over EXR-specific keys."""
        settings = {
            'render.resolution_x': 1920,
            'render.resolution_y': 1080,
            'render.image_settings.file_format': 'OPEN_EXR',
            'render.image_settings.color_depth': '32',
            'render.image_settings.quality': 90,
            'cycles.samples': 512,
        }
        result = _build_thumbnail_settings(settings, 'CYCLES')
        assert result['render.image_settings.file_format'] == 'PNG'
        assert result['render.image_settings.color_depth'] == '8'
        assert result['cycles.samples'] == 32
        assert 'render.image_settings.quality' not in result

    def test_missing_resolution_uses_defaults(self):
        """Missing resolution keys should use 1920x1080 defaults."""
        result = _build_thumbnail_settings({}, 'CYCLES')
        assert result['render.resolution_x'] == 480
        assert result['render.resolution_y'] == 270

    def test_resolution_rounded_to_even(self):
        """Odd resolutions should be rounded to even."""
        settings = {
            'render.resolution_x': 1920,
            'render.resolution_y': 803,
        }
        result = _build_thumbnail_settings(settings, 'CYCLES')
        # 480 * 803 / 1920 = 200.75 -> round_even -> 200
        assert result['render.resolution_y'] % 2 == 0


class TestBuildThumbnailOutputPattern:

    def test_exr_to_thumb_png(self):
        pattern = os.path.join('output', 'my_render_####.exr')
        result = _build_thumbnail_output_pattern(pattern)
        assert result.endswith('thumb_my_render_####.png')
        assert 'output' in result

    def test_hdr_to_thumb_png(self):
        pattern = os.path.join('output', 'scene_####.hdr')
        result = _build_thumbnail_output_pattern(pattern)
        assert result.endswith('thumb_scene_####.png')

    def test_preserves_directory(self):
        pattern = os.path.join('worker', 'output', 'tile_####.exr')
        result = _build_thumbnail_output_pattern(pattern)
        dirname = os.path.dirname(result)
        expected_dir = os.path.join('worker', 'output')
        assert dirname == expected_dir


class TestRenderExrHdrThumbnail:

    def test_returns_none_on_subprocess_failure(self, mocker, tmp_path):
        """Thumbnail pass failure returns None (graceful degradation)."""
        mocker.patch(
            'sethlans_worker_agent.config.WORKER_TEMP_DIR',
            str(tmp_path / 'temp')
        )
        mocker.patch(
            'sethlans_worker_agent.config.WORKER_ROOT',
            str(tmp_path)
        )
        mocker.patch(
            'sethlans_worker_agent.blender_thumbnail'
            '.generate_render_config_script',
            return_value='import bpy'
        )
        mocker.patch(
            'sethlans_worker_agent.system_monitor.detect_gpu_devices',
            return_value=[]
        )

        mock_proc = mocker.Mock()
        mock_proc.wait.return_value = 1
        mock_proc.stdout = mocker.Mock()
        mock_proc.stdout.readline = mocker.Mock(return_value='')
        mock_proc.stdout.close = mocker.Mock()
        mock_proc.stderr = mocker.Mock()
        mock_proc.stderr.readline = mocker.Mock(return_value='')
        mock_proc.stderr.close = mocker.Mock()
        mocker.patch('subprocess.Popen', return_value=mock_proc)

        job_data = {
            'id': 99,
            'render_engine': 'CYCLES',
            'render_device': 'CPU',
            'render_settings': {
                'render.resolution_x': 1920,
                'render.resolution_y': 1080,
                'render.image_settings.file_format': 'OPEN_EXR',
            },
        }
        result = render_exr_hdr_thumbnail(
            job_data,
            str(tmp_path / 'blender'),
            str(tmp_path / 'scene.blend'),
            1,
            str(tmp_path / 'output' / 'render_####.exr'),
            None,
        )
        assert result is None

    def test_returns_path_on_success(self, mocker, tmp_path):
        """Successful thumbnail pass returns the file path."""
        mocker.patch(
            'sethlans_worker_agent.config.WORKER_TEMP_DIR',
            str(tmp_path / 'temp')
        )
        mocker.patch(
            'sethlans_worker_agent.config.WORKER_ROOT',
            str(tmp_path)
        )
        mocker.patch(
            'sethlans_worker_agent.blender_thumbnail'
            '.generate_render_config_script',
            return_value='import bpy'
        )
        mocker.patch(
            'sethlans_worker_agent.system_monitor.detect_gpu_devices',
            return_value=[]
        )

        mock_proc = mocker.Mock()
        mock_proc.wait.return_value = 0
        mock_proc.stdout = mocker.Mock()
        mock_proc.stdout.readline = mocker.Mock(return_value='')
        mock_proc.stdout.close = mocker.Mock()
        mock_proc.stderr = mocker.Mock()
        mock_proc.stderr.readline = mocker.Mock(return_value='')
        mock_proc.stderr.close = mocker.Mock()
        mocker.patch('subprocess.Popen', return_value=mock_proc)

        # Create the expected output file
        out_dir = tmp_path / 'output'
        out_dir.mkdir(parents=True)
        thumb_file = out_dir / 'thumb_render_0001.png'
        thumb_file.write_bytes(b'PNG fake')

        job_data = {
            'id': 99,
            'render_engine': 'CYCLES',
            'render_device': 'CPU',
            'render_settings': {
                'render.resolution_x': 1920,
                'render.resolution_y': 1080,
            },
        }
        result = render_exr_hdr_thumbnail(
            job_data,
            str(tmp_path / 'blender'),
            str(tmp_path / 'scene.blend'),
            1,
            str(out_dir / 'render_####.exr'),
            None,
        )
        assert result is not None
        assert 'thumb_render_0001.png' in result

    def test_gpu_index_passed_to_config_script(self, mocker, tmp_path):
        """GPU index from primary render is passed to thumbnail config."""
        mocker.patch(
            'sethlans_worker_agent.config.WORKER_TEMP_DIR',
            str(tmp_path / 'temp')
        )
        mocker.patch(
            'sethlans_worker_agent.config.WORKER_ROOT',
            str(tmp_path)
        )
        mocker.patch(
            'sethlans_worker_agent.system_monitor.detect_gpu_devices',
            return_value=['CUDA']
        )

        mock_gen = mocker.patch(
            'sethlans_worker_agent.blender_thumbnail'
            '.generate_render_config_script',
            return_value='import bpy'
        )

        mock_proc = mocker.Mock()
        mock_proc.wait.return_value = 1
        mock_proc.stdout = mocker.Mock()
        mock_proc.stdout.readline = mocker.Mock(return_value='')
        mock_proc.stdout.close = mocker.Mock()
        mock_proc.stderr = mocker.Mock()
        mock_proc.stderr.readline = mocker.Mock(return_value='')
        mock_proc.stderr.close = mocker.Mock()
        mocker.patch('subprocess.Popen', return_value=mock_proc)

        job_data = {
            'id': 99,
            'render_engine': 'CYCLES',
            'render_device': 'GPU',
            'render_settings': {
                'render.resolution_x': 1920,
                'render.resolution_y': 1080,
            },
        }
        render_exr_hdr_thumbnail(
            job_data,
            str(tmp_path / 'blender'),
            str(tmp_path / 'scene.blend'),
            1,
            str(tmp_path / 'output' / 'render_####.exr'),
            2,  # GPU index
        )
        # Verify generate_render_config_script was called with gpu_index
        call_kwargs = mock_gen.call_args
        assert call_kwargs[1]['gpu_index_override'] == 2

    def test_exception_returns_none(self, mocker, tmp_path):
        """Any exception in thumbnail pass returns None."""
        mocker.patch(
            'sethlans_worker_agent.config.WORKER_TEMP_DIR',
            str(tmp_path / 'temp')
        )
        mocker.patch(
            'sethlans_worker_agent.config.WORKER_ROOT',
            str(tmp_path)
        )
        mocker.patch(
            'sethlans_worker_agent.blender_thumbnail'
            '.generate_render_config_script',
            side_effect=RuntimeError("script gen error")
        )
        mocker.patch(
            'sethlans_worker_agent.system_monitor.detect_gpu_devices',
            return_value=[]
        )

        job_data = {
            'id': 99,
            'render_engine': 'CYCLES',
            'render_device': 'CPU',
            'render_settings': {},
        }
        result = render_exr_hdr_thumbnail(
            job_data,
            str(tmp_path / 'blender'),
            str(tmp_path / 'scene.blend'),
            1,
            str(tmp_path / 'output' / 'render_####.exr'),
            None,
        )
        assert result is None
