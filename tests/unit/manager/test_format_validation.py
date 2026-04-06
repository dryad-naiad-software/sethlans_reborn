# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for render_settings validation utilities, magic-bytes
checking, and image assembler format configuration.
"""

import io

import pytest

from workers.constants import RenderSettings


# ---- validate_render_settings ----

class TestValidateRenderSettings:

    def _validate(self, settings):
        from workers.serializers.validation_utils import validate_render_settings
        return validate_render_settings(settings)

    def test_valid_format(self):
        result = self._validate({RenderSettings.IMAGE_FILE_FORMAT: 'JPEG'})
        assert result[RenderSettings.IMAGE_FILE_FORMAT] == 'JPEG'

    def test_invalid_format_raises(self):
        from rest_framework.exceptions import ValidationError
        with pytest.raises(ValidationError, match="Invalid output format"):
            self._validate({RenderSettings.IMAGE_FILE_FORMAT: 'GIF'})

    def test_valid_quality(self):
        result = self._validate({RenderSettings.IMAGE_QUALITY: 90})
        assert result[RenderSettings.IMAGE_QUALITY] == 90

    def test_quality_boundary_low(self):
        result = self._validate({RenderSettings.IMAGE_QUALITY: 1})
        assert result[RenderSettings.IMAGE_QUALITY] == 1

    def test_quality_boundary_high(self):
        result = self._validate({RenderSettings.IMAGE_QUALITY: 100})
        assert result[RenderSettings.IMAGE_QUALITY] == 100

    def test_quality_too_low_raises(self):
        from rest_framework.exceptions import ValidationError
        with pytest.raises(ValidationError, match="JPEG quality"):
            self._validate({RenderSettings.IMAGE_QUALITY: 0})

    def test_quality_too_high_raises(self):
        from rest_framework.exceptions import ValidationError
        with pytest.raises(ValidationError, match="JPEG quality"):
            self._validate({RenderSettings.IMAGE_QUALITY: 101})

    def test_quality_not_int_raises(self):
        from rest_framework.exceptions import ValidationError
        with pytest.raises(ValidationError, match="JPEG quality"):
            self._validate({RenderSettings.IMAGE_QUALITY: 90.5})

    def test_valid_color_depth_16(self):
        result = self._validate({RenderSettings.IMAGE_COLOR_DEPTH: '16'})
        assert result[RenderSettings.IMAGE_COLOR_DEPTH] == '16'

    def test_valid_color_depth_32(self):
        result = self._validate({RenderSettings.IMAGE_COLOR_DEPTH: '32'})
        assert result[RenderSettings.IMAGE_COLOR_DEPTH] == '32'

    def test_invalid_color_depth_raises(self):
        from rest_framework.exceptions import ValidationError
        with pytest.raises(ValidationError, match="Color depth"):
            self._validate({RenderSettings.IMAGE_COLOR_DEPTH: '8'})

    def test_none_settings_returns_none(self):
        assert self._validate(None) is None

    def test_empty_dict_returns_empty(self):
        assert self._validate({}) == {}

    def test_no_format_keys_passes(self):
        result = self._validate({RenderSettings.RESOLUTION_X: 1920})
        assert result == {RenderSettings.RESOLUTION_X: 1920}


# ---- validate_output_pattern_extension ----

class TestValidateOutputPatternExtension:

    def _validate(self, data):
        from workers.serializers.validation_utils import validate_output_pattern_extension
        validate_output_pattern_extension(data)

    def test_matching_png_passes(self):
        self._validate({
            'output_file_pattern': 'render_####.png',
            'render_settings': {RenderSettings.IMAGE_FILE_FORMAT: 'PNG'},
        })

    def test_matching_jpeg_passes(self):
        self._validate({
            'output_file_pattern': 'render_####.jpg',
            'render_settings': {RenderSettings.IMAGE_FILE_FORMAT: 'JPEG'},
        })

    def test_mismatched_extension_raises(self):
        from rest_framework.exceptions import ValidationError
        with pytest.raises(ValidationError, match="does not match"):
            self._validate({
                'output_file_pattern': 'render_####.png',
                'render_settings': {RenderSettings.IMAGE_FILE_FORMAT: 'JPEG'},
            })

    def test_no_pattern_passes(self):
        self._validate({
            'render_settings': {RenderSettings.IMAGE_FILE_FORMAT: 'JPEG'},
        })

    def test_no_format_defaults_to_png(self):
        self._validate({
            'output_file_pattern': 'render_####.png',
            'render_settings': {},
        })

    def test_default_format_mismatched_raises(self):
        from rest_framework.exceptions import ValidationError
        with pytest.raises(ValidationError, match="does not match"):
            self._validate({
                'output_file_pattern': 'render_####.jpg',
                'render_settings': {},
            })


# ---- Upload helpers: _check_exr_hdr_magic ----

class TestCheckExrHdrMagic:

    def _check(self, data):
        from workers.views.upload_helpers import _check_exr_hdr_magic
        return _check_exr_hdr_magic(io.BytesIO(data))

    def test_exr_magic_bytes(self):
        data = b'\x76\x2f\x31\x01' + b'\x00' * 12
        assert self._check(data) == 'exr'

    def test_hdr_radiance_magic(self):
        data = b'#?RADIANCE' + b'\x00' * 6
        assert self._check(data) == 'hdr'

    def test_hdr_rgbe_magic(self):
        data = b'#?RGBE' + b'\x00' * 10
        assert self._check(data) == 'hdr'

    def test_png_magic_returns_none(self):
        data = b'\x89PNG\r\n\x1a\n' + b'\x00' * 8
        assert self._check(data) is None

    def test_empty_returns_none(self):
        assert self._check(b'') is None

    def test_file_position_reset(self):
        from workers.views.upload_helpers import _check_exr_hdr_magic
        f = io.BytesIO(b'\x76\x2f\x31\x01' + b'\x00' * 12)
        f.seek(5)
        _check_exr_hdr_magic(f)
        assert f.tell() == 0


# ---- Image assembler _get_format_config ----

class TestGetFormatConfig:

    def _get_config(self, render_settings):
        from workers.image_assembler import _get_format_config
        return _get_format_config(render_settings)

    def test_default_png(self):
        pillow_fmt, ext, mode, kwargs = self._get_config({})
        assert pillow_fmt == 'PNG'
        assert ext == '.png'
        assert mode == 'RGBA'
        assert kwargs == {}

    def test_jpeg_format(self):
        settings = {RenderSettings.IMAGE_FILE_FORMAT: 'JPEG'}
        pillow_fmt, ext, mode, kwargs = self._get_config(settings)
        assert pillow_fmt == 'JPEG'
        assert ext == '.jpg'
        assert mode == 'RGB'
        assert kwargs == {'quality': 90}

    def test_jpeg_custom_quality(self):
        settings = {
            RenderSettings.IMAGE_FILE_FORMAT: 'JPEG',
            RenderSettings.IMAGE_QUALITY: 75,
        }
        _, _, _, kwargs = self._get_config(settings)
        assert kwargs == {'quality': 75}

    def test_jpeg_quality_clamped_low(self):
        settings = {
            RenderSettings.IMAGE_FILE_FORMAT: 'JPEG',
            RenderSettings.IMAGE_QUALITY: -5,
        }
        _, _, _, kwargs = self._get_config(settings)
        assert kwargs == {'quality': 1}

    def test_jpeg_quality_clamped_high(self):
        settings = {
            RenderSettings.IMAGE_FILE_FORMAT: 'JPEG',
            RenderSettings.IMAGE_QUALITY: 200,
        }
        _, _, _, kwargs = self._get_config(settings)
        assert kwargs == {'quality': 100}

    def test_bmp_uses_rgb_mode(self):
        settings = {RenderSettings.IMAGE_FILE_FORMAT: 'BMP'}
        _, _, mode, _ = self._get_config(settings)
        assert mode == 'RGB'

    def test_tiff_uses_rgba_mode(self):
        settings = {RenderSettings.IMAGE_FILE_FORMAT: 'TIFF'}
        _, _, mode, _ = self._get_config(settings)
        assert mode == 'RGBA'

    def test_targa_format(self):
        settings = {RenderSettings.IMAGE_FILE_FORMAT: 'TARGA'}
        pillow_fmt, ext, mode, _ = self._get_config(settings)
        assert pillow_fmt == 'TGA'
        assert ext == '.tga'
        assert mode == 'RGBA'
