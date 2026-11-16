# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the color depth / file format rule in render_settings (#243).

Blender accepts only some ``color_depth`` values for each file format; the
manager rejects any other combination at submission.
"""

import pytest
from rest_framework.exceptions import ValidationError

from workers.constants import FORMAT_COLOR_DEPTHS, OutputFormat, RenderSettings
from workers.serializers import validation_utils
from workers.serializers.validation_utils import (
    COLOR_DEPTH_VALUES,
    validate_render_settings,
)

FMT = RenderSettings.IMAGE_FILE_FORMAT
DEPTH = RenderSettings.IMAGE_COLOR_DEPTH
QUALITY = RenderSettings.IMAGE_QUALITY

# The measured table, written out here so a change to the constant is seen.
EXPECTED_TABLE = {
    'PNG': ('8', '16'),
    'JPEG': ('8',),
    'OPEN_EXR': ('16', '32'),
    'OPEN_EXR_MULTILAYER': ('16', '32'),
    'TIFF': ('8', '16'),
    'BMP': ('8',),
    'HDR': ('32',),
    'TARGA': ('8',),
}
ALL_COMBINATIONS = [
    (fmt, depth, depth in allowed)
    for fmt, allowed in EXPECTED_TABLE.items()
    for depth in ('8', '16', '32')
]


def _message(exc_info):
    return str(exc_info.value.detail[0])


class TestTable:

    def test_table_matches_measurements(self):
        assert FORMAT_COLOR_DEPTHS == EXPECTED_TABLE

    def test_table_covers_every_output_format(self):
        assert set(FORMAT_COLOR_DEPTHS) == set(OutputFormat.values)

    def test_depth_values_are_the_union(self):
        assert COLOR_DEPTH_VALUES == ('8', '16', '32')


class TestFormatAndDepth:

    @pytest.mark.parametrize('fmt,depth,allowed', ALL_COMBINATIONS)
    def test_every_combination(self, fmt, depth, allowed):
        settings = {FMT: fmt, DEPTH: depth}
        if allowed:
            assert validate_render_settings(settings) == settings
            return
        with pytest.raises(ValidationError) as exc_info:
            validate_render_settings(settings)
        message = _message(exc_info)
        assert f"Color depth '{depth}'" in message
        assert f"output format '{fmt}'" in message
        expected = ', '.join(f"'{d}'" for d in EXPECTED_TABLE[fmt])
        assert f"Allowed color depths for '{fmt}': {expected}." in message

    def test_message_text(self):
        with pytest.raises(ValidationError) as exc_info:
            validate_render_settings({FMT: 'JPEG', DEPTH: '16'})
        assert _message(exc_info) == (
            "Color depth '16' is not supported for output format 'JPEG'. "
            "Allowed color depths for 'JPEG': '8'."
        )


class TestDepthWithoutFormat:

    @pytest.mark.parametrize('depth', ['8', '16'])
    def test_depth_allowed_for_default_png(self, depth):
        assert validate_render_settings({DEPTH: depth}) == {DEPTH: depth}

    def test_32_rejected_against_default_png(self):
        with pytest.raises(ValidationError) as exc_info:
            validate_render_settings({DEPTH: '32'})
        assert _message(exc_info) == (
            "Color depth '32' is not supported for output format 'PNG' "
            "(the default when no file format is set). "
            "Allowed color depths for 'PNG': '8', '16'."
        )


class TestExplicitNull:
    """A present key with a null value is rejected; an absent key is not."""

    @pytest.mark.parametrize('key,other', [
        (FMT, {DEPTH: '16'}),
        (QUALITY, {FMT: 'JPEG'}),
        (DEPTH, {FMT: 'JPEG'}),
    ])
    def test_null_rejected(self, key, other):
        with pytest.raises(ValidationError) as exc_info:
            validate_render_settings({**other, key: None})
        assert _message(exc_info) == (
            f"'{key}' must not be null; leave the key out to use the default."
        )

    def test_null_format_rejected_before_depth_rule(self):
        # Formerly treated as the default PNG; the worker would assign None.
        with pytest.raises(ValidationError, match="must not be null"):
            validate_render_settings({FMT: None, DEPTH: '32'})


class TestDepthValue:

    @pytest.mark.parametrize('depth', [16, 32, 8, '64', '', 'sixteen', 16.0])
    def test_value_outside_strings_rejected(self, depth):
        with pytest.raises(ValidationError) as exc_info:
            validate_render_settings({FMT: 'OPEN_EXR', DEPTH: depth})
        assert _message(exc_info) == "Color depth must be one of '8', '16', '32' (a string)."


class TestUnknownFormat:

    def test_unknown_format_rejected_as_before(self):
        with pytest.raises(ValidationError) as exc_info:
            validate_render_settings({FMT: 'GIF', DEPTH: '16'})
        message = _message(exc_info)
        assert message.startswith("Invalid output format 'GIF'.")
        assert 'Color depth' not in message

    def test_format_without_table_entry_gets_no_depth_rule(self, monkeypatch):
        table = {k: v for k, v in FORMAT_COLOR_DEPTHS.items() if k != 'HDR'}
        monkeypatch.setattr(validation_utils, 'FORMAT_COLOR_DEPTHS', table)
        settings = {FMT: 'HDR', DEPTH: '8'}
        assert validate_render_settings(settings) == settings
