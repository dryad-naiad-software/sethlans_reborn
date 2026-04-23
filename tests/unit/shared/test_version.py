# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for :mod:`shared.version`.

Locks in the fix for issue #106: a single source of truth for the
project version string, readable in both source and frozen (PyInstaller)
modes, cached across calls, with a safe fallback when the file is
missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared import version as version_mod


@pytest.fixture(autouse=True)
def _reset_cache():
    """Wipe the module-level cache between tests."""
    version_mod._cached_version = None
    version_mod._warned_missing = False
    yield
    version_mod._cached_version = None
    version_mod._warned_missing = False


class TestSourceMode:
    """Source mode reads <project_root>/VERSION."""

    def test_source_mode_reads_version_file(self, mocker):
        mocker.patch.object(version_mod, "is_frozen", return_value=False)
        # Real repo root is two parents up from this file.
        repo_root = Path(__file__).resolve().parents[3]
        expected = (repo_root / "VERSION").read_text(encoding="utf-8").strip()
        assert version_mod.get_version() == expected


class TestFrozenMode:
    """Frozen mode reads sys._MEIPASS/VERSION."""

    def test_frozen_mode_reads_from_meipass(self, mocker, tmp_path):
        (tmp_path / "VERSION").write_text("9.9.9\n", encoding="utf-8")
        mocker.patch.object(version_mod, "is_frozen", return_value=True)
        mocker.patch.object(
            version_mod.sys, "_MEIPASS", str(tmp_path), create=True,
        )
        assert version_mod.get_version() == "9.9.9"


class TestFallback:
    """Missing file returns the safe fallback, never raises."""

    def test_missing_version_file_returns_fallback(self, mocker, tmp_path):
        bogus = tmp_path / "does-not-exist"
        mocker.patch.object(version_mod, "is_frozen", return_value=False)
        mocker.patch.object(
            version_mod, "get_app_dir", return_value=bogus,
        )
        result = version_mod.get_version()
        assert result.startswith("0.0.0")
        assert result == version_mod._FALLBACK_VERSION


class TestStripping:
    """Trailing whitespace / newlines are stripped."""

    def test_version_is_stripped(self, mocker, tmp_path):
        (tmp_path / "VERSION").write_text("0.2.0\n", encoding="utf-8")
        mocker.patch.object(version_mod, "is_frozen", return_value=True)
        mocker.patch.object(
            version_mod.sys, "_MEIPASS", str(tmp_path), create=True,
        )
        assert version_mod.get_version() == "0.2.0"


class TestCaching:
    """Repeat calls do not re-read the file."""

    def test_cached_across_calls(self, mocker, tmp_path):
        (tmp_path / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        mocker.patch.object(version_mod, "is_frozen", return_value=True)
        mocker.patch.object(
            version_mod.sys, "_MEIPASS", str(tmp_path), create=True,
        )
        spy = mocker.spy(Path, "read_text")
        first = version_mod.get_version()
        second = version_mod.get_version()
        third = version_mod.get_version()
        assert first == second == third == "1.0.0"
        assert spy.call_count == 1
