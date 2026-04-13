# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``worker/sethlans_worker_agent/env_cleanup.py``.

Covers the ``clean_env_for_blender`` function and its helpers:
returning None in dev mode, stripping PyInstaller-injected variables,
removing Python-related keys, cleaning library paths, and preserving
normal environment variables.
"""

import os
import sys

import pytest

from sethlans_worker_agent.env_cleanup import (
    _is_frozen,
    _should_remove_key,
    _strip_internal_paths,
    clean_env_for_blender,
)


# ---- _is_frozen() ----------------------------------------------------------

class TestIsFrozen:

    def test_returns_false_in_dev_mode(self):
        assert _is_frozen() is False

    def test_returns_true_when_frozen(self, mocker):
        mocker.patch.object(sys, 'frozen', True, create=True)
        assert _is_frozen() is True


# ---- _should_remove_key() --------------------------------------------------

class TestShouldRemoveKey:

    @pytest.mark.parametrize("key", [
        "LD_LIBRARY_PATH_MEIPASS",
        "SOME_MEI_VAR",
        "PYINSTALLER_TEMP",
        "my_meipass_var",       # case-insensitive
        "pyinstaller_debug",    # case-insensitive
    ])
    def test_keys_with_markers_are_flagged(self, key):
        assert _should_remove_key(key) is True

    @pytest.mark.parametrize("key", [
        "HOME",
        "USER",
        "PATH",
        "DISPLAY",
        "SOME_NORMAL_VAR",
    ])
    def test_normal_keys_are_not_flagged(self, key):
        assert _should_remove_key(key) is False


# ---- _strip_internal_paths() -----------------------------------------------

class TestStripInternalPaths:

    def test_removes_internal_entries(self):
        path_str = os.pathsep.join([
            "/usr/lib",
            "/tmp/_MEIxxxxxx/_internal/lib",
            "/usr/local/lib",
        ])
        result = _strip_internal_paths(path_str)
        entries = result.split(os.pathsep)
        assert "/usr/lib" in entries
        assert "/usr/local/lib" in entries
        assert len(entries) == 2

    def test_preserves_all_when_no_internal(self):
        path_str = os.pathsep.join(["/usr/lib", "/usr/local/lib"])
        result = _strip_internal_paths(path_str)
        assert result == path_str

    def test_empty_string_returns_empty(self):
        assert _strip_internal_paths("") == ""

    def test_all_internal_entries_removed(self):
        path_str = os.pathsep.join([
            "/tmp/_internal/a",
            "/tmp/_internal/b",
        ])
        result = _strip_internal_paths(path_str)
        assert result == ""


# ---- clean_env_for_blender() — dev mode ------------------------------------

class TestCleanEnvDevMode:
    """In development mode, clean_env_for_blender returns None."""

    def test_returns_none_when_not_frozen(self):
        result = clean_env_for_blender()
        assert result is None

    def test_returns_none_with_blend_file_path(self):
        result = clean_env_for_blender(blend_file_path="/some/scene.blend")
        assert result is None


# ---- clean_env_for_blender() — frozen mode ---------------------------------

class TestCleanEnvFrozenMode:
    """Frozen mode: returns a cleaned dict."""

    @pytest.fixture(autouse=True)
    def _freeze(self, mocker):
        mocker.patch.object(sys, 'frozen', True, create=True)
        mocker.patch.object(
            sys, 'executable',
            '/opt/sethlans/bin/manager/run_manager',
        )

    def test_returns_dict_when_frozen(self):
        result = clean_env_for_blender()
        assert isinstance(result, dict)

    def test_removes_pythonpath(self, monkeypatch):
        monkeypatch.setenv("PYTHONPATH", "/some/path")
        result = clean_env_for_blender()
        assert "PYTHONPATH" not in result

    def test_removes_pythonhome(self, monkeypatch):
        monkeypatch.setenv("PYTHONHOME", "/some/path")
        result = clean_env_for_blender()
        assert "PYTHONHOME" not in result

    def test_removes_tcl_library(self, monkeypatch):
        monkeypatch.setenv("TCL_LIBRARY", "/tmp/_MEI/tcl")
        result = clean_env_for_blender()
        assert "TCL_LIBRARY" not in result

    def test_removes_tk_library(self, monkeypatch):
        monkeypatch.setenv("TK_LIBRARY", "/tmp/_MEI/tk")
        result = clean_env_for_blender()
        assert "TK_LIBRARY" not in result

    def test_removes_meipass_keys(self, monkeypatch):
        monkeypatch.setenv("LD_LIBRARY_PATH_MEIPASS", "/junk")
        result = clean_env_for_blender()
        assert "LD_LIBRARY_PATH_MEIPASS" not in result

    def test_removes_mei_keys(self, monkeypatch):
        monkeypatch.setenv("SOME_MEI_VAR", "junk")
        result = clean_env_for_blender()
        assert "SOME_MEI_VAR" not in result

    def test_removes_pyinstaller_keys(self, monkeypatch):
        monkeypatch.setenv("PYINSTALLER_TEMP", "/tmp")
        result = clean_env_for_blender()
        assert "PYINSTALLER_TEMP" not in result

    def test_preserves_normal_env_vars(self, monkeypatch):
        monkeypatch.setenv("HOME", "/home/artist")
        monkeypatch.setenv("USER", "artist")
        result = clean_env_for_blender()
        assert result.get("HOME") == "/home/artist"
        assert result.get("USER") == "artist"


# ---- Library path stripping by platform ------------------------------------

class TestCleanEnvLinuxLibPaths:

    @pytest.fixture(autouse=True)
    def _freeze_linux(self, mocker):
        mocker.patch.object(sys, 'frozen', True, create=True)
        mocker.patch.object(
            sys, 'executable',
            '/opt/sethlans/bin/manager/run_manager',
        )
        mocker.patch(
            'sethlans_worker_agent.env_cleanup.platform.system',
            return_value='Linux',
        )

    def test_strips_internal_from_ld_library_path(self, monkeypatch):
        path_val = os.pathsep.join([
            "/usr/lib",
            "/tmp/_MEI123/_internal/lib",
        ])
        monkeypatch.setenv("LD_LIBRARY_PATH", path_val)
        result = clean_env_for_blender()
        assert "_internal" not in result.get("LD_LIBRARY_PATH", "")
        assert "/usr/lib" in result.get("LD_LIBRARY_PATH", "")

    def test_removes_ld_library_path_when_all_internal(self, monkeypatch):
        path_val = "/tmp/_MEI123/_internal/lib"
        monkeypatch.setenv("LD_LIBRARY_PATH", path_val)
        result = clean_env_for_blender()
        assert "LD_LIBRARY_PATH" not in result

    def test_empty_ld_library_path_removed(self, monkeypatch):
        monkeypatch.setenv("LD_LIBRARY_PATH", "")
        result = clean_env_for_blender()
        # Empty string is falsy, so the key is deleted
        assert "LD_LIBRARY_PATH" not in result


class TestCleanEnvDarwinLibPaths:

    @pytest.fixture(autouse=True)
    def _freeze_darwin(self, mocker):
        mocker.patch.object(sys, 'frozen', True, create=True)
        mocker.patch.object(
            sys, 'executable',
            '/Applications/Sethlans.app/Contents/MacOS/run_manager',
        )
        mocker.patch(
            'sethlans_worker_agent.env_cleanup.platform.system',
            return_value='Darwin',
        )

    def test_strips_internal_from_dyld_library_path(self, monkeypatch):
        path_val = os.pathsep.join([
            "/usr/lib",
            "/tmp/_MEI123/_internal/lib",
        ])
        monkeypatch.setenv("DYLD_LIBRARY_PATH", path_val)
        result = clean_env_for_blender()
        assert "_internal" not in result.get("DYLD_LIBRARY_PATH", "")

    def test_strips_internal_from_dyld_fallback(self, monkeypatch):
        path_val = os.pathsep.join([
            "/usr/local/lib",
            "/tmp/_MEI/_internal/frameworks",
        ])
        monkeypatch.setenv("DYLD_FALLBACK_LIBRARY_PATH", path_val)
        result = clean_env_for_blender()
        val = result.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        assert "_internal" not in val
        assert "/usr/local/lib" in val


# ---- PATH stripping --------------------------------------------------------

class TestCleanEnvPathStripping:

    @pytest.fixture(autouse=True)
    def _freeze(self, mocker):
        mocker.patch.object(sys, 'frozen', True, create=True)
        mocker.patch.object(
            sys, 'executable', '/opt/sethlans/run_manager',
        )

    def test_strips_internal_from_path(self, monkeypatch):
        path_val = os.pathsep.join([
            "/usr/bin",
            "/tmp/_MEI123/_internal/bin",
            "/usr/local/bin",
        ])
        monkeypatch.setenv("PATH", path_val)
        result = clean_env_for_blender()
        entries = result["PATH"].split(os.pathsep)
        assert "/usr/bin" in entries
        assert "/usr/local/bin" in entries
        assert not any("_internal" in e for e in entries)

    def test_preserves_path_without_internal(self, monkeypatch):
        path_val = os.pathsep.join(["/usr/bin", "/usr/local/bin"])
        monkeypatch.setenv("PATH", path_val)
        result = clean_env_for_blender()
        assert result["PATH"] == path_val
