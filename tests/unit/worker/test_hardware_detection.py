# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for worker/sethlans_worker_agent/cpu_detection.py.

Covers get_cpu_name() with cross-platform fallbacks (AC-10, AC-15).
"""

import subprocess
from unittest.mock import mock_open

MODULE = 'sethlans_worker_agent.cpu_detection'


class TestGetCpuName:
    """Tests for get_cpu_name() — AC-10, AC-15."""

    def test_linux_falls_back_to_proc_cpuinfo(self, mocker):
        """AC-10: On Linux, reads /proc/cpuinfo for CPU name."""
        mocker.patch(f'{MODULE}.platform.system', return_value='Linux')
        cpuinfo = 'processor\t: 0\nmodel name\t: AMD Ryzen 9 5950X\nflags\t: sse\n'
        mocker.patch('builtins.open', mock_open(read_data=cpuinfo))
        from sethlans_worker_agent.cpu_detection import get_cpu_name
        assert get_cpu_name() == 'AMD Ryzen 9 5950X'

    def test_linux_proc_cpuinfo_missing_uses_processor(self, mocker):
        """AC-10: If /proc/cpuinfo is missing, falls back to platform.processor()."""
        mocker.patch(f'{MODULE}.platform.system', return_value='Linux')
        mocker.patch('builtins.open', side_effect=OSError('No such file'))
        mocker.patch(f'{MODULE}.platform.processor', return_value='x86_64')
        from sethlans_worker_agent.cpu_detection import get_cpu_name
        assert get_cpu_name() == 'x86_64'

    def test_linux_all_fallbacks_fail_returns_unknown(self, mocker):
        """AC-10: If all fallbacks fail, returns 'Unknown'."""
        mocker.patch(f'{MODULE}.platform.system', return_value='Linux')
        mocker.patch('builtins.open', side_effect=OSError('No such file'))
        mocker.patch(f'{MODULE}.platform.processor', return_value='')
        from sethlans_worker_agent.cpu_detection import get_cpu_name
        assert get_cpu_name() == 'Unknown'

    def test_windows_uses_registry(self, mocker):
        """Windows reads CPU brand from the registry."""
        mocker.patch(f'{MODULE}.platform.system', return_value='Windows')
        mocker.patch(
            f'{MODULE}._get_cpu_name_windows',
            return_value='AMD Ryzen 7 3700X 8-Core Processor',
        )
        from sethlans_worker_agent.cpu_detection import get_cpu_name
        assert get_cpu_name() == 'AMD Ryzen 7 3700X 8-Core Processor'

    def test_windows_registry_fails_falls_back_to_processor(self, mocker):
        """Windows falls back to platform.processor() if registry fails."""
        mocker.patch(f'{MODULE}.platform.system', return_value='Windows')
        mocker.patch(f'{MODULE}._get_cpu_name_windows', return_value=None)
        mocker.patch('builtins.open', side_effect=OSError)
        mocker.patch(f'{MODULE}.platform.processor',
                     return_value='AMD64 Family 23 Model 113')
        from sethlans_worker_agent.cpu_detection import get_cpu_name
        assert get_cpu_name() == 'AMD64 Family 23 Model 113'

    def test_arm_on_macos_uses_sysctl_fallback(self, mocker):
        """AC-15: On macOS with 'arm', falls back to sysctl brand string."""
        mocker.patch(f'{MODULE}.platform.processor', return_value='arm')
        mocker.patch(f'{MODULE}.platform.system', return_value='Darwin')
        mock_result = mocker.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'Apple M1 Pro\n'
        mocker.patch(f'{MODULE}.subprocess.run', return_value=mock_result)
        from sethlans_worker_agent.cpu_detection import get_cpu_name
        assert get_cpu_name() == 'Apple M1 Pro'

    def test_aarch64_on_macos_uses_sysctl_fallback(self, mocker):
        """AC-15: 'aarch64' also triggers macOS sysctl fallback."""
        mocker.patch(f'{MODULE}.platform.processor', return_value='aarch64')
        mocker.patch(f'{MODULE}.platform.system', return_value='Darwin')
        mock_result = mocker.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'Apple M2 Max\n'
        mocker.patch(f'{MODULE}.subprocess.run', return_value=mock_result)
        from sethlans_worker_agent.cpu_detection import get_cpu_name
        assert get_cpu_name() == 'Apple M2 Max'

    def test_macos_sysctl_failure_falls_back_to_arm(self, mocker):
        """If sysctl fails on macOS, returns the original 'arm' string."""
        mocker.patch(f'{MODULE}.platform.processor', return_value='arm')
        mocker.patch(f'{MODULE}.platform.system', return_value='Darwin')
        mocker.patch(
            f'{MODULE}.subprocess.run',
            side_effect=OSError('sysctl not found'),
        )
        # Also fail /proc/cpuinfo (not on macOS anyway)
        mocker.patch('builtins.open', side_effect=OSError)
        from sethlans_worker_agent.cpu_detection import get_cpu_name
        assert get_cpu_name() == 'arm'

    def test_macos_sysctl_timeout_falls_back_to_arm(self, mocker):
        """Timeout from sysctl does not crash, falls back gracefully."""
        mocker.patch(f'{MODULE}.platform.processor', return_value='arm')
        mocker.patch(f'{MODULE}.platform.system', return_value='Darwin')
        mocker.patch(
            f'{MODULE}.subprocess.run',
            side_effect=subprocess.TimeoutExpired(cmd='sysctl', timeout=5),
        )
        mocker.patch('builtins.open', side_effect=OSError)
        from sethlans_worker_agent.cpu_detection import get_cpu_name
        assert get_cpu_name() == 'arm'

    def test_macos_sysctl_empty_stdout_falls_back(self, mocker):
        """sysctl returns 0 but empty stdout: falls back to 'arm'."""
        mocker.patch(f'{MODULE}.platform.processor', return_value='arm')
        mocker.patch(f'{MODULE}.platform.system', return_value='Darwin')
        mock_result = mocker.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '   \n'
        mocker.patch(f'{MODULE}.subprocess.run', return_value=mock_result)
        mocker.patch('builtins.open', side_effect=OSError)
        from sethlans_worker_agent.cpu_detection import get_cpu_name
        assert get_cpu_name() == 'arm'

    def test_macos_sysctl_nonzero_return_falls_back(self, mocker):
        """sysctl returns non-zero: falls back."""
        mocker.patch(f'{MODULE}.platform.processor', return_value='arm')
        mocker.patch(f'{MODULE}.platform.system', return_value='Darwin')
        mock_result = mocker.MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ''
        mocker.patch(f'{MODULE}.subprocess.run', return_value=mock_result)
        mocker.patch('builtins.open', side_effect=OSError)
        from sethlans_worker_agent.cpu_detection import get_cpu_name
        assert get_cpu_name() == 'arm'

    def test_arm_on_linux_reads_proc_cpuinfo(self, mocker):
        """'arm' on Linux skips sysctl and reads /proc/cpuinfo."""
        mocker.patch(f'{MODULE}.platform.processor', return_value='arm')
        mocker.patch(f'{MODULE}.platform.system', return_value='Linux')
        cpuinfo = 'model name\t: Cortex-A72\n'
        mocker.patch('builtins.open', mock_open(read_data=cpuinfo))
        from sethlans_worker_agent.cpu_detection import get_cpu_name
        assert get_cpu_name() == 'Cortex-A72'

    def test_all_fallbacks_fail_empty_processor_returns_unknown(self, mocker):
        """Empty processor + all fallbacks fail = 'Unknown'."""
        mocker.patch(f'{MODULE}.platform.system', return_value='Windows')
        mocker.patch(f'{MODULE}._get_cpu_name_windows', return_value=None)
        mocker.patch('builtins.open', side_effect=OSError)
        mocker.patch(f'{MODULE}.platform.processor', return_value='')
        from sethlans_worker_agent.cpu_detection import get_cpu_name
        assert get_cpu_name() == 'Unknown'


class TestGetCpuNameMacos:
    """Tests for _get_cpu_name_macos() helper."""

    def test_returns_brand_string(self, mocker):
        mock_result = mocker.MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'Apple M1 Pro\n'
        mocker.patch(f'{MODULE}.subprocess.run', return_value=mock_result)
        from sethlans_worker_agent.cpu_detection import _get_cpu_name_macos
        assert _get_cpu_name_macos() == 'Apple M1 Pro'

    def test_returns_none_on_oserror(self, mocker):
        mocker.patch(f'{MODULE}.subprocess.run', side_effect=OSError)
        from sethlans_worker_agent.cpu_detection import _get_cpu_name_macos
        assert _get_cpu_name_macos() is None

    def test_returns_none_on_timeout(self, mocker):
        mocker.patch(
            f'{MODULE}.subprocess.run',
            side_effect=subprocess.TimeoutExpired(cmd='sysctl', timeout=5),
        )
        from sethlans_worker_agent.cpu_detection import _get_cpu_name_macos
        assert _get_cpu_name_macos() is None


class TestGetCpuNameWindows:
    """Tests for _get_cpu_name_windows() helper."""

    def test_returns_brand_from_registry(self, mocker):
        mock_winreg = mocker.MagicMock()
        mock_winreg.QueryValueEx.return_value = (
            'AMD Ryzen 7 3700X 8-Core Processor', 1
        )
        mocker.patch.dict('sys.modules', {'winreg': mock_winreg})
        from sethlans_worker_agent.cpu_detection import _get_cpu_name_windows
        assert _get_cpu_name_windows() == 'AMD Ryzen 7 3700X 8-Core Processor'

    def test_returns_none_on_import_error(self, mocker):
        mocker.patch.dict('sys.modules', {'winreg': None})
        from sethlans_worker_agent.cpu_detection import _get_cpu_name_windows
        # winreg=None in sys.modules causes ImportError on import
        result = _get_cpu_name_windows()
        assert result is None

    def test_returns_none_on_oserror(self, mocker):
        mock_winreg = mocker.MagicMock()
        mock_winreg.OpenKey.side_effect = OSError('Registry error')
        mocker.patch.dict('sys.modules', {'winreg': mock_winreg})
        from sethlans_worker_agent.cpu_detection import _get_cpu_name_windows
        assert _get_cpu_name_windows() is None

    def test_strips_whitespace(self, mocker):
        mock_winreg = mocker.MagicMock()
        mock_winreg.QueryValueEx.return_value = (
            '  Intel Core i9-13900K   ', 1
        )
        mocker.patch.dict('sys.modules', {'winreg': mock_winreg})
        from sethlans_worker_agent.cpu_detection import _get_cpu_name_windows
        assert _get_cpu_name_windows() == 'Intel Core i9-13900K'


class TestGetCpuNameLinux:
    """Tests for _get_cpu_name_linux() helper."""

    def test_returns_model_name(self, mocker):
        cpuinfo = 'processor\t: 0\nmodel name\t: AMD EPYC 7763\nflags\t: sse\n'
        mocker.patch('builtins.open', mock_open(read_data=cpuinfo))
        from sethlans_worker_agent.cpu_detection import _get_cpu_name_linux
        assert _get_cpu_name_linux() == 'AMD EPYC 7763'

    def test_returns_none_when_file_missing(self, mocker):
        mocker.patch('builtins.open', side_effect=OSError)
        from sethlans_worker_agent.cpu_detection import _get_cpu_name_linux
        assert _get_cpu_name_linux() is None

    def test_returns_none_when_no_model_name_line(self, mocker):
        cpuinfo = 'processor\t: 0\nflags\t: sse\n'
        mocker.patch('builtins.open', mock_open(read_data=cpuinfo))
        from sethlans_worker_agent.cpu_detection import _get_cpu_name_linux
        assert _get_cpu_name_linux() is None
