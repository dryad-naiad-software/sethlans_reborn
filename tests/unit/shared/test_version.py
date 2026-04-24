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
        # Real repo root is three parents up from this file
        # (tests/unit/shared/test_version.py).
        repo_root = Path(__file__).resolve().parents[3]
        version_path = repo_root / "VERSION"
        module_path = version_mod._resolve_version_path()
        if not version_path.is_file() or not module_path.is_file():
            # Diagnostic snapshot: the Linux self-hosted GH Actions
            # runner lives under a ``_work`` tree that may contain
            # symlinks. If ``resolve()`` leaks through a symlink the
            # computed repo_root (via parents[3]) or the module's
            # get_app_dir()-derived path can diverge from the actual
            # checkout. Log both so we can pin down which one drifts.
            parents = [str(p) for p in Path(__file__).resolve().parents[:5]]
            pytest.fail(
                f"VERSION path resolution failed.\n"
                f"  test-side version_path={version_path!s} "
                f"exists={version_path.exists()}\n"
                f"  module-side _resolve_version_path()={module_path!s} "
                f"exists={module_path.exists()}\n"
                f"  repo_root={repo_root!s} exists={repo_root.exists()}\n"
                f"  test_file={Path(__file__)!s}\n"
                f"  test_file_resolved={Path(__file__).resolve()!s}\n"
                f"  parents[:5]={parents}\n"
                f"  cwd={Path.cwd()!s}\n"
            )
        expected = version_path.read_text(encoding="utf-8").strip()
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
