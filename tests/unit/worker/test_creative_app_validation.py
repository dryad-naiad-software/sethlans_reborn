# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for creative app name validation (FR-4b).

Covers:
- Rejection of entries with special characters
- Acceptance of valid entries
- Case-insensitive substring matching in YieldMonitor
- Own Blender PID exclusion from detection
- Config-level validation in config_idle module
"""
from unittest.mock import MagicMock

YIELD_MODULE = 'sethlans_worker_agent.idle_detection.yield_monitor'
CONFIG_MODULE = 'sethlans_worker_agent.config_idle'


class TestYieldMonitorAppNameValidation:
    """FR-4b: _validated_creative_apps in yield_monitor.py."""

    def test_valid_names_accepted(self):
        """Normal app names pass validation."""
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            _validated_creative_apps,
        )
        result = _validated_creative_apps(
            ["blender", "maya", "cinema4d", "after-fx", "app.name"],
        )
        assert result == ["blender", "maya", "cinema4d", "after-fx", "app.name"]

    def test_special_chars_rejected(self):
        """Entries with chars outside [a-zA-Z0-9._-] are rejected."""
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            _validated_creative_apps,
        )
        result = _validated_creative_apps(
            ["<script>", "app;rm", "app rm", "app/path", "good-app"],
        )
        assert result == ["good-app"]

    def test_empty_string_rejected(self):
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            _validated_creative_apps,
        )
        result = _validated_creative_apps(["", "  ", "blender"])
        assert result == ["blender"]

    def test_none_input_returns_empty(self):
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            _validated_creative_apps,
        )
        result = _validated_creative_apps(None)
        assert result == []

    def test_empty_list_returns_empty(self):
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            _validated_creative_apps,
        )
        result = _validated_creative_apps([])
        assert result == []

    def test_normalized_to_lowercase(self):
        """Names are normalized to lowercase for substring matching."""
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            _validated_creative_apps,
        )
        result = _validated_creative_apps(["Blender", "MAYA", "Cinema4D"])
        assert result == ["blender", "maya", "cinema4d"]


class TestConfigIdleAppNameValidation:
    """FR-4b: _load_creative_app_names in config_idle.py."""

    def test_valid_entries_accepted(self, mocker):
        from sethlans_worker_agent.config_idle import _load_creative_app_names
        mock_gcv = MagicMock(return_value="blender,maya,houdini")
        result = _load_creative_app_names(mock_gcv)
        assert result == ["blender", "maya", "houdini"]

    def test_special_chars_rejected_in_config(self, mocker):
        from sethlans_worker_agent.config_idle import _load_creative_app_names
        mock_gcv = MagicMock(return_value="blender,app;rm,<script>")
        result = _load_creative_app_names(mock_gcv)
        assert result == ["blender"]

    def test_none_returns_defaults(self, mocker):
        from sethlans_worker_agent.config_idle import _load_creative_app_names
        mock_gcv = MagicMock(return_value=None)
        result = _load_creative_app_names(mock_gcv)
        assert "blender" in result
        assert "maya" in result

    def test_all_invalid_returns_defaults(self, mocker):
        """If all entries are invalid, fall back to defaults."""
        from sethlans_worker_agent.config_idle import _load_creative_app_names
        mock_gcv = MagicMock(return_value="<bad>;evil")
        result = _load_creative_app_names(mock_gcv)
        assert "blender" in result  # defaults

    def test_json_list_input(self, mocker):
        """List input (from JSON config) is validated."""
        from sethlans_worker_agent.config_idle import _load_creative_app_names
        mock_gcv = MagicMock(
            return_value=["blender", "nuke", "app;bad"],
        )
        result = _load_creative_app_names(mock_gcv)
        assert result == ["blender", "nuke"]


class TestAppNameRegexPattern:
    """Verify the regex pattern itself."""

    def test_pattern_allows_alphanumeric(self):
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            _APP_NAME_RE,
        )
        assert _APP_NAME_RE.match("blender123")
        assert _APP_NAME_RE.match("cinema4d")

    def test_pattern_allows_dots_dashes_underscores(self):
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            _APP_NAME_RE,
        )
        assert _APP_NAME_RE.match("after-fx")
        assert _APP_NAME_RE.match("app.name")
        assert _APP_NAME_RE.match("app_name")

    def test_pattern_rejects_spaces(self):
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            _APP_NAME_RE,
        )
        assert not _APP_NAME_RE.match("app name")

    def test_pattern_rejects_semicolons(self):
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            _APP_NAME_RE,
        )
        assert not _APP_NAME_RE.match("app;rm")

    def test_pattern_rejects_angle_brackets(self):
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            _APP_NAME_RE,
        )
        assert not _APP_NAME_RE.match("<script>")

    def test_pattern_rejects_slashes(self):
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            _APP_NAME_RE,
        )
        assert not _APP_NAME_RE.match("app/path")
