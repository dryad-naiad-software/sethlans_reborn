# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``launcher/apply_pending_setup.py`` curated env + frozen
manager-dir resolution.

Covers Spec 2 security LOW follow-ups (review ``6688ada``):

* ``_manager_dir`` defers to ``shared.frozen_paths`` in frozen mode so
  PyInstaller's runtime extraction directory does not break the
  source-mode relative path.
* ``build_curated_env`` keeps POSIX strict (HOME-only) but expands the
  Windows allowlist to cover stdlib expectations (``USERPROFILE``,
  ``LOCALAPPDATA``, ``APPDATA``, ``TEMP``, ``TMP``, ``COMSPEC``,
  ``PATHEXT``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from launcher import apply_pending_setup as apply_mod


# ---- Fix 1: frozen-aware _manager_dir() -----------------------------------

class TestManagerDir:

    def test_source_mode_returns_project_manager_dir(self, mocker):
        """Source mode: parent.parent of launcher/ resolves to manager/."""
        # Make absolutely sure is_frozen reports False even if some
        # other test polluted sys.frozen.
        mocker.patch(
            "shared.frozen_paths.is_frozen", return_value=False,
        )
        result = apply_mod._manager_dir()
        # In source mode the launcher file is <project>/launcher/...
        # so the manager dir is <project>/manager/.
        expected = Path(apply_mod.__file__).resolve().parent.parent / "manager"
        assert result == expected

    def test_frozen_mode_returns_frozen_paths_manager_dir(
        self, mocker, tmp_path,
    ):
        """Frozen mode: defer to frozen_paths.get_manager_dir()."""
        bundle_root = tmp_path / "install" / "bin" / "manager"
        bundle_root.mkdir(parents=True)
        # Frozen-mode plumbing: sys.frozen + sys.executable inside the
        # bundle so frozen_paths.get_manager_dir() resolves there.
        mocker.patch.object(sys, "frozen", True, create=True)
        mocker.patch.object(
            sys, "executable", str(bundle_root / "run_manager.exe"),
        )
        # is_frozen() relies on sys.frozen; frozen_paths uses it too.
        result = apply_mod._manager_dir()
        # Must NOT be the source-mode relative path.
        source_relative = (
            Path(apply_mod.__file__).resolve().parent.parent / "manager"
        )
        assert result != source_relative
        # Must equal the bundled manager dir (executable parent).
        assert result == bundle_root.resolve()


# ---- Fix 2: Windows-curated env minimalism --------------------------------

class TestCuratedEnvWindows:

    @pytest.fixture(autouse=True)
    def _force_windows(self, mocker):
        mocker.patch.object(apply_mod.platform, "system", return_value="Windows")

    def test_includes_extended_windows_allowlist(self, mocker, tmp_path):
        """All stdlib-required Windows env vars are forwarded."""
        env_in = {
            "SystemRoot": r"C:\Windows",
            "USERPROFILE": r"C:\Users\u",
            "LOCALAPPDATA": r"C:\Users\u\AppData\Local",
            "APPDATA": r"C:\Users\u\AppData\Roaming",
            "TEMP": r"C:\Users\u\AppData\Local\Temp",
            "TMP": r"C:\Users\u\AppData\Local\Temp",
            "COMSPEC": r"C:\Windows\system32\cmd.exe",
            "PATHEXT": ".COM;.EXE;.BAT;.CMD",
            # Polluting var that MUST be stripped.
            "DJANGO_DEBUG": "1",
        }
        with patch.dict(apply_mod.os.environ, env_in, clear=True):
            curated = apply_mod.build_curated_env(manager_dir=tmp_path)
        # Required allowlist: each present.
        for key in (
            "SystemRoot", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
            "TEMP", "TMP", "COMSPEC", "PATHEXT",
        ):
            assert curated[key] == env_in[key], f"missing {key}"
        # Universal trio.
        assert curated["DJANGO_SETTINGS_MODULE"] == apply_mod.DJANGO_SETTINGS_MODULE
        assert "PATH" in curated
        assert curated["PYTHONPATH"] == str(tmp_path)
        # Polluting var must NOT have leaked through.
        assert "DJANGO_DEBUG" not in curated

    def test_falls_back_to_safe_defaults_when_unset(self, tmp_path):
        """Missing Windows vars get sensible fallbacks rather than KeyError."""
        with patch.dict(apply_mod.os.environ, {}, clear=True):
            curated = apply_mod.build_curated_env(manager_dir=tmp_path)
        # Fallbacks documented in build_curated_env.
        assert curated["COMSPEC"] == r"C:\Windows\system32\cmd.exe"
        assert curated["PATHEXT"] == ".COM;.EXE;.BAT;.CMD"
        # SystemRoot has its own fallback.
        assert curated["SystemRoot"] == r"C:\Windows"
        # Empty-string fallbacks for the remaining keys.
        for key in ("USERPROFILE", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP"):
            assert curated[key] == ""

    def test_no_home_on_windows(self, tmp_path):
        """``HOME`` is POSIX-only — must be absent on Windows."""
        with patch.dict(apply_mod.os.environ, {}, clear=True):
            curated = apply_mod.build_curated_env(manager_dir=tmp_path)
        assert "HOME" not in curated


class TestCuratedEnvPosix:

    @pytest.fixture(autouse=True)
    def _force_linux(self, mocker):
        mocker.patch.object(apply_mod.platform, "system", return_value="Linux")

    def test_posix_only_has_home_plus_universal(self, tmp_path):
        """POSIX: only HOME + the universal trio.  No Windows leakage."""
        env_in = {"HOME": "/home/u", "PATH": "/usr/bin:/bin"}
        with patch.dict(apply_mod.os.environ, env_in, clear=True):
            curated = apply_mod.build_curated_env(manager_dir=tmp_path)
        assert curated["HOME"] == "/home/u"
        assert curated["DJANGO_SETTINGS_MODULE"] == apply_mod.DJANGO_SETTINGS_MODULE
        assert "PATH" in curated
        assert curated["PYTHONPATH"] == str(tmp_path)
        # Set difference: keys present must equal {DJANGO_SETTINGS_MODULE,
        # PATH, PYTHONPATH, HOME}.  No Windows-only vars sneak in.
        forbidden = {
            "SystemRoot", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
            "TEMP", "TMP", "COMSPEC", "PATHEXT",
        }
        assert forbidden.isdisjoint(set(curated.keys()))
