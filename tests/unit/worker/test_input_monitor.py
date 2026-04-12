# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for worker/sethlans_worker_agent/idle_detection/input_monitor.py.

Covers per-platform input idle time detection (FR-1):
- Windows: GetLastInputInfo + GetTickCount64
- macOS: CGEventSourceSecondsSinceLastEventType
- Linux X11: XScreenSaverQueryInfo under _xlib_lock
- Linux Wayland: busctl IdleHint via subprocess
- Graceful None when APIs unavailable
"""
import subprocess
from unittest.mock import MagicMock

import pytest

MODULE = 'sethlans_worker_agent.idle_detection.input_monitor'


class TestWindowsIdleDetection:
    """FR-1a: Windows GetLastInputInfo + GetTickCount64."""

    def test_uses_get_tick_count_64_not_32(self):
        """FR-1a: Must use GetTickCount64 to avoid 49-day wraparound."""
        import inspect
        from sethlans_worker_agent.idle_detection import input_monitor
        src = inspect.getsource(input_monitor._get_idle_seconds_windows)
        assert 'GetTickCount64' in src
        # Verify GetTickCount (32-bit) is NOT used outside GetTickCount64
        stripped = src.replace('GetTickCount64', '')
        assert 'GetTickCount()' not in stripped

    def test_returns_none_on_exception(self, mocker):
        """Any exception inside the function -> None."""
        # Patch 'ctypes' at sys.modules level so the local import fails
        import sys as real_sys
        mocker.patch.dict(real_sys.modules, {'ctypes': None})
        from sethlans_worker_agent.idle_detection.input_monitor import (
            _get_idle_seconds_windows,
        )
        result = _get_idle_seconds_windows()
        assert result is None

    def test_returns_float_on_this_platform(self):
        """On Windows (this CI platform), the function returns a float."""
        import sys
        from sethlans_worker_agent.idle_detection.input_monitor import (
            _get_idle_seconds_windows,
        )
        if sys.platform != 'win32':
            pytest.skip("Windows-only test")
        result = _get_idle_seconds_windows()
        assert isinstance(result, float)
        assert result >= 0


class TestMacOSIdleDetection:
    """FR-1b: macOS CGEventSourceSecondsSinceLastEventType."""

    def test_returns_none_when_coregraphics_unavailable(self, mocker):
        mock_ctypes = MagicMock()
        mock_util = MagicMock()
        mock_util.find_library.return_value = None
        mock_ctypes.util = mock_util
        mocker.patch.dict('sys.modules', {
            'ctypes': mock_ctypes,
            'ctypes.util': mock_util,
        })
        from sethlans_worker_agent.idle_detection.input_monitor import (
            _get_idle_seconds_macos,
        )
        assert _get_idle_seconds_macos() is None


class TestLinuxX11IdleDetection:
    """FR-1c: XScreenSaverQueryInfo under _xlib_lock."""

    def test_returns_none_without_display_env(self, mocker):
        """No DISPLAY env -> None."""
        mocker.patch.dict('os.environ', {}, clear=True)
        from sethlans_worker_agent.idle_detection.input_monitor import (
            _get_idle_seconds_linux_x11,
        )
        assert _get_idle_seconds_linux_x11() is None

    def test_returns_none_when_libs_missing(self, mocker):
        """Missing libXss or libX11 -> None."""
        mocker.patch.dict('os.environ', {'DISPLAY': ':0'})
        mock_ctypes = MagicMock()
        mock_util = MagicMock()
        mock_util.find_library.return_value = None
        mock_ctypes.util = mock_util
        mocker.patch.dict('sys.modules', {
            'ctypes': mock_ctypes,
            'ctypes.util': mock_util,
        })
        from sethlans_worker_agent.idle_detection.input_monitor import (
            _get_idle_seconds_linux_x11,
        )
        assert _get_idle_seconds_linux_x11() is None

    def test_xlib_lock_is_used(self):
        """FR-1c: _xlib_lock exists as a threading.Lock at module level."""
        import threading
        from sethlans_worker_agent.idle_detection.input_monitor import (
            _xlib_lock,
        )
        assert isinstance(_xlib_lock, type(threading.Lock()))


class TestLinuxDbusIdleDetection:
    """FR-1d: Wayland fallback via busctl IdleHint."""

    def test_idle_true_returns_inf(self, mocker):
        """busctl reports 'b true' -> float('inf')."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "b true\n"
        mocker.patch(f'{MODULE}.subprocess.run', return_value=mock_result)
        from sethlans_worker_agent.idle_detection.input_monitor import (
            _get_idle_seconds_linux_dbus,
        )
        assert _get_idle_seconds_linux_dbus() == float('inf')

    def test_idle_false_returns_zero(self, mocker):
        """busctl reports 'b false' -> 0.0."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "b false\n"
        mocker.patch(f'{MODULE}.subprocess.run', return_value=mock_result)
        from sethlans_worker_agent.idle_detection.input_monitor import (
            _get_idle_seconds_linux_dbus,
        )
        assert _get_idle_seconds_linux_dbus() == 0.0

    def test_busctl_not_found_returns_none(self, mocker):
        """FileNotFoundError from busctl -> None."""
        mocker.patch(
            f'{MODULE}.subprocess.run',
            side_effect=FileNotFoundError,
        )
        from sethlans_worker_agent.idle_detection.input_monitor import (
            _get_idle_seconds_linux_dbus,
        )
        assert _get_idle_seconds_linux_dbus() is None

    def test_busctl_timeout_returns_none(self, mocker):
        """subprocess.TimeoutExpired -> None."""
        mocker.patch(
            f'{MODULE}.subprocess.run',
            side_effect=subprocess.TimeoutExpired(cmd='busctl', timeout=5),
        )
        from sethlans_worker_agent.idle_detection.input_monitor import (
            _get_idle_seconds_linux_dbus,
        )
        assert _get_idle_seconds_linux_dbus() is None

    def test_nonzero_returncode_returns_none(self, mocker):
        """Non-zero exit code -> None."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mocker.patch(f'{MODULE}.subprocess.run', return_value=mock_result)
        from sethlans_worker_agent.idle_detection.input_monitor import (
            _get_idle_seconds_linux_dbus,
        )
        assert _get_idle_seconds_linux_dbus() is None


class TestDispatch:
    """get_seconds_since_last_input dispatches to the correct platform."""

    def test_unsupported_platform_returns_none(self, mocker):
        mocker.patch(f'{MODULE}.sys.platform', 'freebsd12')
        from sethlans_worker_agent.idle_detection.input_monitor import (
            get_seconds_since_last_input,
        )
        assert get_seconds_since_last_input() is None

    def test_linux_tries_x11_then_dbus(self, mocker):
        """On Linux, tries X11 first, then falls back to D-Bus."""
        mocker.patch(f'{MODULE}.sys.platform', 'linux')
        x11_mock = mocker.patch(
            f'{MODULE}._get_idle_seconds_linux_x11', return_value=None,
        )
        dbus_mock = mocker.patch(
            f'{MODULE}._get_idle_seconds_linux_dbus', return_value=42.0,
        )
        from sethlans_worker_agent.idle_detection.input_monitor import (
            get_seconds_since_last_input,
        )
        result = get_seconds_since_last_input()
        assert result == 42.0
        x11_mock.assert_called_once()
        dbus_mock.assert_called_once()

    def test_linux_x11_success_skips_dbus(self, mocker):
        mocker.patch(f'{MODULE}.sys.platform', 'linux')
        mocker.patch(
            f'{MODULE}._get_idle_seconds_linux_x11', return_value=5.0,
        )
        dbus_mock = mocker.patch(
            f'{MODULE}._get_idle_seconds_linux_dbus',
        )
        from sethlans_worker_agent.idle_detection.input_monitor import (
            get_seconds_since_last_input,
        )
        result = get_seconds_since_last_input()
        assert result == 5.0
        dbus_mock.assert_not_called()
