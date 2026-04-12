# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for worker/sethlans_worker_agent/idle_detection/headless.py.

Covers headless and Docker environment detection (FR-3):
- Linux: no DISPLAY + no WAYLAND_DISPLAY + /dev/dri present
- Windows: no explorer.exe process
- macOS: no WindowServer process
- Docker: /.dockerenv or 'docker' in /proc/1/cgroup
"""
from pathlib import Path
from unittest.mock import MagicMock

MODULE = 'sethlans_worker_agent.idle_detection.headless'


class TestDockerDetection:
    """FR-3c: Docker containers detected via /.dockerenv or cgroup."""

    def test_dockerenv_exists_returns_true(self, mocker):
        mocker.patch(f'{MODULE}.Path.exists', return_value=True)
        from sethlans_worker_agent.idle_detection.headless import _is_docker
        assert _is_docker() is True

    def test_docker_in_cgroup_returns_true(self, mocker):
        # /.dockerenv does not exist
        def mock_exists(self):
            path_str = str(self)
            if path_str == '/.dockerenv':
                return False
            if 'cgroup' in path_str:
                return True
            return False

        mocker.patch.object(Path, 'exists', mock_exists)
        mocker.patch.object(
            Path, 'read_text',
            return_value='12:cpuset:/docker/abc123\n',
        )
        from sethlans_worker_agent.idle_detection.headless import _is_docker
        assert _is_docker() is True

    def test_no_docker_indicators_returns_false(self, mocker):
        mocker.patch.object(Path, 'exists', return_value=False)
        from sethlans_worker_agent.idle_detection.headless import _is_docker
        assert _is_docker() is False


class TestHeadlessLinux:
    """FR-3a: Linux headless when no DISPLAY, no WAYLAND, but /dev/dri."""

    def test_no_display_no_wayland_with_dri_is_headless(self, mocker):
        mocker.patch.dict('os.environ', {}, clear=True)
        mocker.patch.object(Path, 'exists', return_value=True)  # /dev/dri
        from sethlans_worker_agent.idle_detection.headless import (
            _is_headless_linux,
        )
        assert _is_headless_linux() is True

    def test_display_set_is_not_headless(self, mocker):
        mocker.patch.dict(
            'os.environ', {'DISPLAY': ':0'}, clear=True,
        )
        mocker.patch.object(Path, 'exists', return_value=True)
        from sethlans_worker_agent.idle_detection.headless import (
            _is_headless_linux,
        )
        assert _is_headless_linux() is False

    def test_wayland_display_set_is_not_headless(self, mocker):
        mocker.patch.dict(
            'os.environ', {'WAYLAND_DISPLAY': 'wayland-0'}, clear=True,
        )
        mocker.patch.object(Path, 'exists', return_value=True)
        from sethlans_worker_agent.idle_detection.headless import (
            _is_headless_linux,
        )
        assert _is_headless_linux() is False

    def test_no_display_no_dri_is_not_headless(self, mocker):
        """No DISPLAY and no /dev/dri -> not a GPU render node."""
        mocker.patch.dict('os.environ', {}, clear=True)
        mocker.patch.object(Path, 'exists', return_value=False)
        from sethlans_worker_agent.idle_detection.headless import (
            _is_headless_linux,
        )
        assert _is_headless_linux() is False


class TestHeadlessWindows:
    """FR-3a: Windows headless when no explorer.exe."""

    def test_no_explorer_is_headless(self, mocker):
        """No explorer.exe running -> headless (Windows Server)."""
        mock_proc = MagicMock()
        mock_proc.info = {'name': 'svchost.exe'}
        mocker.patch(
            'psutil.process_iter',
            return_value=[mock_proc],
        )
        from sethlans_worker_agent.idle_detection.headless import (
            _is_headless_windows,
        )
        assert _is_headless_windows() is True

    def test_explorer_running_is_not_headless(self, mocker):
        """explorer.exe present -> interactive desktop."""
        mock_proc = MagicMock()
        mock_proc.info = {'name': 'explorer.exe'}
        mocker.patch(
            'psutil.process_iter',
            return_value=[mock_proc],
        )
        from sethlans_worker_agent.idle_detection.headless import (
            _is_headless_windows,
        )
        assert _is_headless_windows() is False

    def test_explorer_case_insensitive(self, mocker):
        """explorer.exe detection is case-insensitive."""
        mock_proc = MagicMock()
        mock_proc.info = {'name': 'Explorer.EXE'}
        mocker.patch(
            'psutil.process_iter',
            return_value=[mock_proc],
        )
        from sethlans_worker_agent.idle_detection.headless import (
            _is_headless_windows,
        )
        assert _is_headless_windows() is False


class TestHeadlessMacOS:
    """FR-3a: macOS headless when no WindowServer."""

    def test_no_windowserver_is_headless(self, mocker):
        mock_proc = MagicMock()
        mock_proc.info = {'name': 'launchd'}
        mocker.patch(
            'psutil.process_iter',
            return_value=[mock_proc],
        )
        from sethlans_worker_agent.idle_detection.headless import (
            _is_headless_macos,
        )
        assert _is_headless_macos() is True

    def test_windowserver_running_is_not_headless(self, mocker):
        mock_proc = MagicMock()
        mock_proc.info = {'name': 'WindowServer'}
        mocker.patch(
            'psutil.process_iter',
            return_value=[mock_proc],
        )
        from sethlans_worker_agent.idle_detection.headless import (
            _is_headless_macos,
        )
        assert _is_headless_macos() is False


class TestIsHeadless:
    """FR-3: Composite is_headless() with Docker priority."""

    def test_docker_checked_first(self, mocker):
        """Docker detection has priority over platform checks."""
        mocker.patch(f'{MODULE}._is_docker', return_value=True)
        mocker.patch(f'{MODULE}.sys.platform', 'linux')
        linux_mock = mocker.patch(f'{MODULE}._is_headless_linux')
        from sethlans_worker_agent.idle_detection.headless import is_headless
        assert is_headless() is True
        linux_mock.assert_not_called()

    def test_linux_platform_dispatch(self, mocker):
        mocker.patch(f'{MODULE}._is_docker', return_value=False)
        mocker.patch(f'{MODULE}.sys.platform', 'linux')
        mocker.patch(f'{MODULE}._is_headless_linux', return_value=True)
        from sethlans_worker_agent.idle_detection.headless import is_headless
        assert is_headless() is True

    def test_win32_platform_dispatch(self, mocker):
        mocker.patch(f'{MODULE}._is_docker', return_value=False)
        mocker.patch(f'{MODULE}.sys.platform', 'win32')
        mocker.patch(f'{MODULE}._is_headless_windows', return_value=False)
        from sethlans_worker_agent.idle_detection.headless import is_headless
        assert is_headless() is False

    def test_unknown_platform_returns_false(self, mocker):
        mocker.patch(f'{MODULE}._is_docker', return_value=False)
        mocker.patch(f'{MODULE}.sys.platform', 'freebsd12')
        from sethlans_worker_agent.idle_detection.headless import is_headless
        assert is_headless() is False
