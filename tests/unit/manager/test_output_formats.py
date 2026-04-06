# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for output format constants in manager/workers/constants.py.

Verifies OutputFormat enum values, FORMAT_EXTENSIONS mapping,
PILLOW_COMPATIBLE_FORMATS frozenset, PILLOW_FORMAT_NAMES mapping,
and new RenderSettings image keys.
"""

from workers.constants import (
    FORMAT_EXTENSIONS,
    OutputFormat,
    PILLOW_COMPATIBLE_FORMATS,
    PILLOW_FORMAT_NAMES,
    RenderSettings,
)


# ---- OutputFormat enum ----

class TestOutputFormat:
    def test_choices_count(self):
        assert len(OutputFormat.choices) == 8

    def test_png_value(self):
        assert OutputFormat.PNG.value == 'PNG'

    def test_jpeg_value(self):
        assert OutputFormat.JPEG.value == 'JPEG'

    def test_open_exr_value(self):
        assert OutputFormat.OPEN_EXR.value == 'OPEN_EXR'

    def test_open_exr_multilayer_value(self):
        assert OutputFormat.OPEN_EXR_MULTILAYER.value == 'OPEN_EXR_MULTILAYER'

    def test_tiff_value(self):
        assert OutputFormat.TIFF.value == 'TIFF'

    def test_bmp_value(self):
        assert OutputFormat.BMP.value == 'BMP'

    def test_hdr_value(self):
        assert OutputFormat.HDR.value == 'HDR'

    def test_targa_value(self):
        assert OutputFormat.TARGA.value == 'TARGA'

    def test_all_values_are_strings(self):
        for val in OutputFormat.values:
            assert isinstance(val, str)


# ---- FORMAT_EXTENSIONS ----

class TestFormatExtensions:
    def test_png_extension(self):
        assert FORMAT_EXTENSIONS['PNG'] == '.png'

    def test_jpeg_extension(self):
        assert FORMAT_EXTENSIONS['JPEG'] == '.jpg'

    def test_open_exr_extension(self):
        assert FORMAT_EXTENSIONS['OPEN_EXR'] == '.exr'

    def test_open_exr_multilayer_extension(self):
        assert FORMAT_EXTENSIONS['OPEN_EXR_MULTILAYER'] == '.exr'

    def test_tiff_extension(self):
        assert FORMAT_EXTENSIONS['TIFF'] == '.tif'

    def test_bmp_extension(self):
        assert FORMAT_EXTENSIONS['BMP'] == '.bmp'

    def test_hdr_extension(self):
        assert FORMAT_EXTENSIONS['HDR'] == '.hdr'

    def test_targa_extension(self):
        assert FORMAT_EXTENSIONS['TARGA'] == '.tga'

    def test_all_output_formats_have_extensions(self):
        for val in OutputFormat.values:
            assert val in FORMAT_EXTENSIONS

    def test_all_extensions_start_with_dot(self):
        for ext in FORMAT_EXTENSIONS.values():
            assert ext.startswith('.')


# ---- PILLOW_COMPATIBLE_FORMATS ----

class TestPillowCompatibleFormats:
    def test_is_frozenset(self):
        assert isinstance(PILLOW_COMPATIBLE_FORMATS, frozenset)

    def test_contains_png(self):
        assert 'PNG' in PILLOW_COMPATIBLE_FORMATS

    def test_contains_jpeg(self):
        assert 'JPEG' in PILLOW_COMPATIBLE_FORMATS

    def test_contains_tiff(self):
        assert 'TIFF' in PILLOW_COMPATIBLE_FORMATS

    def test_contains_bmp(self):
        assert 'BMP' in PILLOW_COMPATIBLE_FORMATS

    def test_contains_targa(self):
        assert 'TARGA' in PILLOW_COMPATIBLE_FORMATS

    def test_does_not_contain_exr(self):
        assert 'OPEN_EXR' not in PILLOW_COMPATIBLE_FORMATS

    def test_does_not_contain_exr_multilayer(self):
        assert 'OPEN_EXR_MULTILAYER' not in PILLOW_COMPATIBLE_FORMATS

    def test_does_not_contain_hdr(self):
        assert 'HDR' not in PILLOW_COMPATIBLE_FORMATS

    def test_count(self):
        assert len(PILLOW_COMPATIBLE_FORMATS) == 5


# ---- PILLOW_FORMAT_NAMES ----

class TestPillowFormatNames:
    def test_png(self):
        assert PILLOW_FORMAT_NAMES['PNG'] == 'PNG'

    def test_jpeg(self):
        assert PILLOW_FORMAT_NAMES['JPEG'] == 'JPEG'

    def test_tiff(self):
        assert PILLOW_FORMAT_NAMES['TIFF'] == 'TIFF'

    def test_bmp(self):
        assert PILLOW_FORMAT_NAMES['BMP'] == 'BMP'

    def test_targa(self):
        assert PILLOW_FORMAT_NAMES['TARGA'] == 'TGA'

    def test_only_pillow_compatible_formats(self):
        assert set(PILLOW_FORMAT_NAMES.keys()) == PILLOW_COMPATIBLE_FORMATS


# ---- RenderSettings image keys ----

class TestRenderSettingsImageKeys:
    def test_image_file_format(self):
        assert RenderSettings.IMAGE_FILE_FORMAT == "render.image_settings.file_format"

    def test_image_quality(self):
        assert RenderSettings.IMAGE_QUALITY == "render.image_settings.quality"

    def test_image_color_depth(self):
        assert RenderSettings.IMAGE_COLOR_DEPTH == "render.image_settings.color_depth"

    def test_image_keys_are_dot_separated(self):
        assert '.' in RenderSettings.IMAGE_FILE_FORMAT
        assert '.' in RenderSettings.IMAGE_QUALITY
        assert '.' in RenderSettings.IMAGE_COLOR_DEPTH
