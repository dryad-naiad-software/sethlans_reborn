# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for manager/workers/serializers/blender_versions.py.

Tests the SERIES_REGEX validation and create() logic with mocked
``latest_patch`` (``shared.blender_sources``).
"""

from unittest.mock import MagicMock, patch

import pytest
from rest_framework import serializers

from shared.blender_sources import AllSourcesFailedError, BlenderSourcesConfigError

from workers.serializers.blender_versions import (
    SERIES_REGEX,
    SupportedBlenderVersionSerializer,
    EffectiveBlenderVersionSerializer,
)


class TestSeriesRegex:
    """Validate the regex that enforces 'major.minor' format."""

    def test_valid_two_digit(self):
        assert SERIES_REGEX.match("4.2") is not None

    def test_valid_single_digits(self):
        assert SERIES_REGEX.match("3.0") is not None

    def test_valid_large_numbers(self):
        assert SERIES_REGEX.match("12.34") is not None

    def test_rejects_three_part_version(self):
        assert SERIES_REGEX.match("4.2.1") is None

    def test_rejects_text(self):
        assert SERIES_REGEX.match("four.two") is None

    def test_rejects_empty(self):
        assert SERIES_REGEX.match("") is None

    def test_rejects_no_minor(self):
        assert SERIES_REGEX.match("4.") is None

    def test_rejects_no_major(self):
        assert SERIES_REGEX.match(".2") is None

    def test_rejects_spaces(self):
        assert SERIES_REGEX.match("4 .2") is None

    def test_rejects_leading_dot(self):
        assert SERIES_REGEX.match(".4.2") is None


class TestSeriesValidation:
    """Test validate_series on the serializer."""

    def test_valid_series_passes(self):
        s = SupportedBlenderVersionSerializer()
        assert s.validate_series("4.2") == "4.2"

    def test_invalid_series_raises(self):
        s = SupportedBlenderVersionSerializer()
        with pytest.raises(serializers.ValidationError):
            s.validate_series("not-a-version")

    def test_three_part_raises(self):
        s = SupportedBlenderVersionSerializer()
        with pytest.raises(serializers.ValidationError):
            s.validate_series("4.2.1")


class TestSerializerCreate:
    """Test the create() method that resolves patch versions."""

    @pytest.mark.parametrize("error, blames_series", [
        (AllSourcesFailedError("Blender release list (series page) 99.99", [
            ("https://download.blender.org/release/", "HTTP 403"),
        ]), True),
        (ValueError("not a Blender series: '99.99'"), True),
        (BlenderSourcesConfigError("SETHLANS_BLENDER_SOURCES entry 1 must use https"), False),
    ], ids=["all-sources-failed", "bad-series", "bad-source-setting"])
    def test_create_turns_a_source_error_into_a_400(self, error, blames_series):
        """BS-REL-9: a ``BlenderSourcesError`` (or ``ValueError``) on create is a 400 on ``series``, not a 500.
        BS-CFG-2: a bad source setting says the setting is invalid, not that the series may not exist."""
        s = SupportedBlenderVersionSerializer()
        with patch("workers.serializers.blender_versions.latest_patch", side_effect=error):
            with pytest.raises(serializers.ValidationError) as exc_info:
                s.create({"series": "99.99", "is_default": False})
        message = str(exc_info.value.detail["series"])
        assert str(error) in message
        assert ("Verify the series exists" in message) is blames_series
        assert ("the Blender source setting is invalid" in message) is not blames_series

    @patch(
        "workers.serializers.blender_versions.latest_patch"
    )
    @patch.object(
        SupportedBlenderVersionSerializer,
        "__init__",
        lambda self, *a, **kw: None,
    )
    def test_create_populates_major_minor(self, mock_resolve):
        mock_resolve.return_value = "4.2.19"

        s = SupportedBlenderVersionSerializer()
        # Mock the parent create to capture validated_data
        captured = {}

        def fake_super_create(validated_data):
            captured.update(validated_data)
            return MagicMock()

        with patch(
            "rest_framework.serializers.ModelSerializer.create",
            side_effect=fake_super_create,
        ):
            s.create({"series": "4.2", "is_default": False})

        assert captured["major"] == 4
        assert captured["minor"] == 2
        assert captured["resolved_version"] == "4.2.19"


class TestEffectiveBlenderVersionSerializer:
    def test_fields_are_read_only(self):
        s = EffectiveBlenderVersionSerializer()
        assert s.fields["series"].read_only is True
        assert s.fields["resolved_version"].read_only is True

    def test_serializes_data(self):
        data = {"series": "4.2", "resolved_version": "4.2.19"}
        s = EffectiveBlenderVersionSerializer(data)
        assert s.data["series"] == "4.2"
        assert s.data["resolved_version"] == "4.2.19"
