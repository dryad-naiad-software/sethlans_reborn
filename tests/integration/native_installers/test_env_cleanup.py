# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Integration tests for Blender environment cleanup in frozen mode.

Verifies that ``env_cleanup.clean_env_for_blender()`` correctly strips
PyInstaller-injected variables, and that both ``blender_executor.py``
and ``blender_thumbnail.py`` pass the cleaned env and blend-file cwd
to ``subprocess.Popen``.

Uses ``monkeypatch`` on ``env_cleanup._is_frozen`` instead of setting
``sys.frozen`` directly, because the attribute does not exist in source
mode and ``monkeypatch.setattr`` requires the attribute to exist.
"""

import os

from sethlans_worker_agent.env_cleanup import (
    _should_remove_key,
    _strip_internal_paths,
    clean_env_for_blender,
)


def _simulate_frozen(monkeypatch):
    """Patch _is_frozen to return True for env_cleanup tests."""
    monkeypatch.setattr(
        'sethlans_worker_agent.env_cleanup._is_frozen', lambda: True,
    )


class TestCleanEnvFrozenMode:
    """clean_env_for_blender strips PyInstaller vars in frozen mode."""

    def test_returns_none_in_source_mode(self, monkeypatch):
        """Returns None when not frozen (source/dev mode)."""
        monkeypatch.setattr(
            'sethlans_worker_agent.env_cleanup._is_frozen',
            lambda: False,
        )
        result = clean_env_for_blender()
        assert result is None

    def test_returns_dict_in_frozen_mode(self, monkeypatch):
        """Returns a dict when frozen."""
        _simulate_frozen(monkeypatch)
        result = clean_env_for_blender()
        assert isinstance(result, dict)

    def test_removes_pythonpath_in_frozen(self, monkeypatch):
        """PYTHONPATH is removed in frozen mode."""
        _simulate_frozen(monkeypatch)
        monkeypatch.setenv('PYTHONPATH', '/some/pyinstaller/path')
        result = clean_env_for_blender()
        assert 'PYTHONPATH' not in result

    def test_removes_pythonhome_in_frozen(self, monkeypatch):
        """PYTHONHOME is removed in frozen mode."""
        _simulate_frozen(monkeypatch)
        monkeypatch.setenv('PYTHONHOME', '/pyinstaller/home')
        result = clean_env_for_blender()
        assert 'PYTHONHOME' not in result

    def test_removes_tcl_tk_library_in_frozen(self, monkeypatch):
        """TCL_LIBRARY and TK_LIBRARY are removed in frozen mode."""
        _simulate_frozen(monkeypatch)
        monkeypatch.setenv('TCL_LIBRARY', '/pyinstaller/tcl')
        monkeypatch.setenv('TK_LIBRARY', '/pyinstaller/tk')
        result = clean_env_for_blender()
        assert 'TCL_LIBRARY' not in result
        assert 'TK_LIBRARY' not in result

    def test_removes_meipass_marker_keys(self, monkeypatch):
        """Keys containing _MEIPASS are removed."""
        _simulate_frozen(monkeypatch)
        monkeypatch.setenv('SOME_MEIPASS_VAR', 'value')
        result = clean_env_for_blender()
        assert 'SOME_MEIPASS_VAR' not in result

    def test_removes_mei_marker_keys(self, monkeypatch):
        """Keys containing _MEI are removed."""
        _simulate_frozen(monkeypatch)
        monkeypatch.setenv('MY_MEI_TEMP', '/tmp/path')
        result = clean_env_for_blender()
        assert 'MY_MEI_TEMP' not in result

    def test_removes_pyinstaller_marker_keys(self, monkeypatch):
        """Keys containing PYINSTALLER are removed."""
        _simulate_frozen(monkeypatch)
        monkeypatch.setenv('PYINSTALLER_CONFIG', 'something')
        result = clean_env_for_blender()
        assert 'PYINSTALLER_CONFIG' not in result

    def test_preserves_normal_env_vars(self, monkeypatch):
        """Normal env vars (PATH, HOME) are preserved."""
        _simulate_frozen(monkeypatch)
        monkeypatch.setenv('MY_SAFE_VAR', 'keep_me')
        result = clean_env_for_blender()
        assert result.get('MY_SAFE_VAR') == 'keep_me'

    def test_strips_internal_from_path(self, monkeypatch):
        """PATH entries containing _internal are removed."""
        _simulate_frozen(monkeypatch)
        sep = os.pathsep
        fake_path = sep.join([
            '/usr/bin',
            '/pyinstaller/_internal/bin',
            '/usr/local/bin',
        ])
        monkeypatch.setenv('PATH', fake_path)
        result = clean_env_for_blender()
        path_entries = result['PATH'].split(sep)
        assert '/usr/bin' in path_entries
        assert '/usr/local/bin' in path_entries
        for entry in path_entries:
            assert '_internal' not in entry

    def test_blend_file_path_arg_accepted(self, monkeypatch):
        """blend_file_path argument is accepted for API compatibility."""
        _simulate_frozen(monkeypatch)
        result = clean_env_for_blender('/some/file.blend')
        assert isinstance(result, dict)


class TestShouldRemoveKey:
    """_should_remove_key detects PyInstaller markers correctly."""

    def test_meipass_key_detected(self):
        assert _should_remove_key('_MEIPASS') is True

    def test_mei_key_detected(self):
        assert _should_remove_key('SOME_MEI_VAR') is True

    def test_pyinstaller_key_detected(self):
        assert _should_remove_key('PYINSTALLER_THING') is True

    def test_case_insensitive(self):
        assert _should_remove_key('my_meipass_lower') is True

    def test_normal_key_not_detected(self):
        assert _should_remove_key('HOME') is False
        assert _should_remove_key('PATH') is False


class TestStripInternalPaths:
    """_strip_internal_paths removes _internal entries."""

    def test_removes_internal_entries(self):
        sep = os.pathsep
        path = sep.join(['/usr/bin', '/app/_internal/lib', '/usr/local'])
        result = _strip_internal_paths(path)
        assert '/app/_internal/lib' not in result
        assert '/usr/bin' in result

    def test_preserves_all_when_no_internal(self):
        sep = os.pathsep
        path = sep.join(['/usr/bin', '/usr/local/bin'])
        result = _strip_internal_paths(path)
        assert result == path

    def test_empty_string_returns_empty(self):
        assert _strip_internal_paths('') == ''


class TestLibPathCleanupByPlatform:
    """Library path vars are cleaned per platform."""

    def test_ld_library_path_cleaned_on_linux(self, monkeypatch):
        """LD_LIBRARY_PATH has _internal entries stripped on Linux."""
        _simulate_frozen(monkeypatch)
        monkeypatch.setattr(
            'sethlans_worker_agent.env_cleanup.platform.system',
            lambda: 'Linux',
        )
        sep = os.pathsep
        monkeypatch.setenv(
            'LD_LIBRARY_PATH',
            sep.join(['/lib', '/app/_internal/lib']),
        )
        result = clean_env_for_blender()
        if 'LD_LIBRARY_PATH' in result:
            assert '_internal' not in result['LD_LIBRARY_PATH']

    def test_dyld_paths_cleaned_on_darwin(self, monkeypatch):
        """DYLD_LIBRARY_PATH entries with _internal stripped on macOS."""
        _simulate_frozen(monkeypatch)
        monkeypatch.setattr(
            'sethlans_worker_agent.env_cleanup.platform.system',
            lambda: 'Darwin',
        )
        sep = os.pathsep
        monkeypatch.setenv(
            'DYLD_LIBRARY_PATH',
            sep.join(['/lib', '/app/_internal/lib']),
        )
        result = clean_env_for_blender()
        if 'DYLD_LIBRARY_PATH' in result:
            assert '_internal' not in result['DYLD_LIBRARY_PATH']


class TestExecutorUsesEnvCleanup:
    """blender_executor and blender_thumbnail pass env and cwd."""

    def test_executor_popen_kwargs_include_env_key(self):
        """blender_executor.py's Popen kwargs include 'env'."""
        import sethlans_worker_agent.blender_executor as mod
        with open(mod.__file__) as f:
            source = f.read()
        assert 'env_cleanup.clean_env_for_blender' in source
        assert '"env": env_cleanup.clean_env_for_blender' in source

    def test_executor_popen_kwargs_include_cwd(self):
        """blender_executor.py's Popen kwargs include 'cwd'."""
        import sethlans_worker_agent.blender_executor as mod
        with open(mod.__file__) as f:
            source = f.read()
        assert '"cwd": os.path.dirname(local_blend_file_path)' in source

    def test_thumbnail_popen_kwargs_include_env_key(self):
        """blender_thumbnail.py's Popen kwargs include 'env'."""
        import sethlans_worker_agent.blender_thumbnail as mod
        with open(mod.__file__) as f:
            source = f.read()
        assert 'env_cleanup.clean_env_for_blender' in source
        assert '"env": env_cleanup.clean_env_for_blender' in source

    def test_thumbnail_popen_kwargs_include_cwd(self):
        """blender_thumbnail.py's Popen kwargs include 'cwd'."""
        import sethlans_worker_agent.blender_thumbnail as mod
        with open(mod.__file__) as f:
            source = f.read()
        assert '"cwd": os.path.dirname(blend_file_path)' in source
