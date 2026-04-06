# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for the format_utils module.

Tests FORMAT_EXTENSIONS mapping and get_format_and_extension() function.
"""
import pytest

from sethlans_worker_agent.format_utils import (
    FORMAT_EXTENSIONS,
    EXR_HDR_FORMATS,
    get_format_and_extension,
)


class TestFormatExtensions:

    def test_all_expected_formats_present(self):
        expected = {
            'PNG', 'JPEG', 'OPEN_EXR', 'OPEN_EXR_MULTILAYER',
            'TIFF', 'BMP', 'HDR', 'TARGA',
        }
        assert set(FORMAT_EXTENSIONS.keys()) == expected

    def test_png_extension(self):
        assert FORMAT_EXTENSIONS['PNG'] == '.png'

    def test_jpeg_extension(self):
        assert FORMAT_EXTENSIONS['JPEG'] == '.jpg'

    def test_exr_extension(self):
        assert FORMAT_EXTENSIONS['OPEN_EXR'] == '.exr'

    def test_exr_multilayer_extension(self):
        assert FORMAT_EXTENSIONS['OPEN_EXR_MULTILAYER'] == '.exr'

    def test_tiff_extension(self):
        assert FORMAT_EXTENSIONS['TIFF'] == '.tif'

    def test_bmp_extension(self):
        assert FORMAT_EXTENSIONS['BMP'] == '.bmp'

    def test_hdr_extension(self):
        assert FORMAT_EXTENSIONS['HDR'] == '.hdr'

    def test_targa_extension(self):
        assert FORMAT_EXTENSIONS['TARGA'] == '.tga'


class TestExrHdrFormats:

    def test_contains_exr_formats(self):
        assert 'OPEN_EXR' in EXR_HDR_FORMATS
        assert 'OPEN_EXR_MULTILAYER' in EXR_HDR_FORMATS

    def test_contains_hdr(self):
        assert 'HDR' in EXR_HDR_FORMATS

    def test_does_not_contain_png(self):
        assert 'PNG' not in EXR_HDR_FORMATS


class TestGetFormatAndExtension:

    def test_returns_png_when_key_missing(self):
        fmt, ext = get_format_and_extension({})
        assert fmt == 'PNG'
        assert ext == '.png'

    def test_returns_png_when_format_unrecognised(self):
        settings = {'render.image_settings.file_format': 'INVALID'}
        fmt, ext = get_format_and_extension(settings)
        assert fmt == 'PNG'
        assert ext == '.png'

    def test_returns_jpeg(self):
        settings = {'render.image_settings.file_format': 'JPEG'}
        fmt, ext = get_format_and_extension(settings)
        assert fmt == 'JPEG'
        assert ext == '.jpg'

    def test_returns_open_exr(self):
        settings = {'render.image_settings.file_format': 'OPEN_EXR'}
        fmt, ext = get_format_and_extension(settings)
        assert fmt == 'OPEN_EXR'
        assert ext == '.exr'

    def test_returns_hdr(self):
        settings = {'render.image_settings.file_format': 'HDR'}
        fmt, ext = get_format_and_extension(settings)
        assert fmt == 'HDR'
        assert ext == '.hdr'

    @pytest.mark.parametrize("format_str,expected_ext", [
        ('PNG', '.png'),
        ('JPEG', '.jpg'),
        ('OPEN_EXR', '.exr'),
        ('OPEN_EXR_MULTILAYER', '.exr'),
        ('TIFF', '.tif'),
        ('BMP', '.bmp'),
        ('HDR', '.hdr'),
        ('TARGA', '.tga'),
    ])
    def test_all_formats(self, format_str, expected_ext):
        settings = {'render.image_settings.file_format': format_str}
        fmt, ext = get_format_and_extension(settings)
        assert fmt == format_str
        assert ext == expected_ext

    def test_empty_string_defaults_to_png(self):
        settings = {'render.image_settings.file_format': ''}
        fmt, ext = get_format_and_extension(settings)
        assert fmt == 'PNG'
        assert ext == '.png'
