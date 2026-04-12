# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for worker/sethlans_worker_agent/idle_detection/contention.py.

Covers GPU and CPU contention checks (FR-2):
- NVIDIA nvidia-smi and AMD rocm-smi subprocess calls
- CPU rolling 3-sample average via psutil
- Threshold logic and graceful degradation
"""
import collections
from unittest.mock import MagicMock

import pytest

MODULE = 'sethlans_worker_agent.idle_detection.contention'


@pytest.fixture(autouse=True)
def _reset_cpu_samples():
    """Clear the module-level _cpu_samples deque between tests."""
    from sethlans_worker_agent.idle_detection import contention
    contention._cpu_samples.clear()
    yield
    contention._cpu_samples.clear()


class TestGpuContention:
    """FR-2a: GPU utilization threshold checks."""

    def test_nvidia_above_threshold_returns_true(self, mocker):
        """nvidia-smi reports 25% -> above 20% threshold."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "25\n"
        mocker.patch(f'{MODULE}.subprocess.run', return_value=mock_result)
        mocker.patch(f'{MODULE}.sys.platform', 'linux')
        from sethlans_worker_agent.idle_detection.contention import (
            check_gpu_contention,
        )
        assert check_gpu_contention(threshold_pct=20) is True

    def test_nvidia_below_threshold_returns_false(self, mocker):
        """nvidia-smi reports 15% -> below 20% threshold."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "15\n"
        mocker.patch(f'{MODULE}.subprocess.run', return_value=mock_result)
        mocker.patch(f'{MODULE}.sys.platform', 'linux')
        from sethlans_worker_agent.idle_detection.contention import (
            check_gpu_contention,
        )
        assert check_gpu_contention(threshold_pct=20) is False

    def test_nvidia_at_threshold_returns_false(self, mocker):
        """Exactly at threshold -> not above, returns False."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "20\n"
        mocker.patch(f'{MODULE}.subprocess.run', return_value=mock_result)
        mocker.patch(f'{MODULE}.sys.platform', 'linux')
        from sethlans_worker_agent.idle_detection.contention import (
            check_gpu_contention,
        )
        assert check_gpu_contention(threshold_pct=20) is False

    def test_multi_gpu_any_above_triggers(self, mocker):
        """Multiple GPUs: any one above threshold -> True."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "10\n55\n5\n"
        mocker.patch(f'{MODULE}.subprocess.run', return_value=mock_result)
        mocker.patch(f'{MODULE}.sys.platform', 'linux')
        from sethlans_worker_agent.idle_detection.contention import (
            check_gpu_contention,
        )
        assert check_gpu_contention(threshold_pct=20) is True

    def test_nvidia_not_found_falls_through_to_amd(self, mocker):
        """nvidia-smi not found -> tries rocm-smi."""
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd[0])
            if cmd[0] == 'nvidia-smi':
                raise FileNotFoundError
            result = MagicMock()
            result.returncode = 0
            result.stdout = "GPU[0] : GPU use(%): 10\n"
            return result

        mocker.patch(f'{MODULE}.subprocess.run', side_effect=mock_run)
        mocker.patch(f'{MODULE}.sys.platform', 'linux')
        from sethlans_worker_agent.idle_detection.contention import (
            check_gpu_contention,
        )
        assert check_gpu_contention(threshold_pct=20) is False
        assert 'nvidia-smi' in calls
        assert 'rocm-smi' in calls

    def test_both_tools_unavailable_returns_false(self, mocker):
        """Neither nvidia-smi nor rocm-smi available -> no contention."""
        mocker.patch(
            f'{MODULE}.subprocess.run', side_effect=FileNotFoundError,
        )
        mocker.patch(f'{MODULE}.sys.platform', 'linux')
        from sethlans_worker_agent.idle_detection.contention import (
            check_gpu_contention,
        )
        assert check_gpu_contention() is False

    def test_macos_skips_gpu_check(self, mocker):
        """macOS: GPU contention always returns False."""
        mocker.patch(f'{MODULE}.sys.platform', 'darwin')
        from sethlans_worker_agent.idle_detection.contention import (
            check_gpu_contention,
        )
        assert check_gpu_contention() is False

    def test_amd_uses_higher_threshold(self, mocker):
        """AMD (rocm-smi) uses max(threshold, 50%)."""
        nvidia_mock = MagicMock()
        nvidia_mock.returncode = 1  # nvidia-smi fails

        amd_mock = MagicMock()
        amd_mock.returncode = 0
        amd_mock.stdout = "GPU[0] : GPU use(%): 45\n"

        def mock_run(cmd, **kwargs):
            if cmd[0] == 'nvidia-smi':
                return nvidia_mock
            return amd_mock

        mocker.patch(f'{MODULE}.subprocess.run', side_effect=mock_run)
        mocker.patch(f'{MODULE}.sys.platform', 'linux')
        from sethlans_worker_agent.idle_detection.contention import (
            check_gpu_contention,
        )
        # 45% < 50% (AMD minimum) -> no contention
        assert check_gpu_contention(threshold_pct=20) is False


class TestCpuContention:
    """FR-2b: CPU rolling 3-sample average."""

    def test_fewer_than_3_samples_returns_false(self, mocker):
        """Fewer than 3 samples -> no contention (insufficient data)."""
        mocker.patch(f'{MODULE}.psutil.cpu_percent', return_value=90.0)
        from sethlans_worker_agent.idle_detection.contention import (
            check_cpu_contention,
        )
        assert check_cpu_contention(70) is False  # 1 sample
        assert check_cpu_contention(70) is False  # 2 samples

    def test_three_samples_above_threshold(self, mocker):
        """3 samples averaging above threshold -> True."""
        values = iter([80.0, 75.0, 90.0])  # avg = 81.67
        mocker.patch(f'{MODULE}.psutil.cpu_percent', side_effect=values)
        from sethlans_worker_agent.idle_detection.contention import (
            check_cpu_contention,
        )
        check_cpu_contention(70)  # 1
        check_cpu_contention(70)  # 2
        assert check_cpu_contention(70) is True  # 3: avg > 70

    def test_three_samples_below_threshold(self, mocker):
        """3 samples averaging below threshold -> False."""
        values = iter([50.0, 60.0, 40.0])  # avg = 50
        mocker.patch(f'{MODULE}.psutil.cpu_percent', side_effect=values)
        from sethlans_worker_agent.idle_detection.contention import (
            check_cpu_contention,
        )
        check_cpu_contention(70)
        check_cpu_contention(70)
        assert check_cpu_contention(70) is False

    def test_rolling_window_drops_old_samples(self, mocker):
        """deque(maxlen=3) drops oldest sample on 4th call."""
        values = iter([90.0, 90.0, 90.0, 10.0])
        mocker.patch(f'{MODULE}.psutil.cpu_percent', side_effect=values)
        from sethlans_worker_agent.idle_detection.contention import (
            check_cpu_contention,
        )
        check_cpu_contention(70)  # [90]
        check_cpu_contention(70)  # [90, 90]
        check_cpu_contention(70)  # [90, 90, 90] avg=90 > 70
        # 4th call: deque becomes [90, 90, 10], avg=63.3 < 70
        assert check_cpu_contention(70) is False

    def test_separate_deque_not_shared(self):
        """FR-2b: _cpu_samples is a separate deque(maxlen=3)."""
        from sethlans_worker_agent.idle_detection.contention import (
            _cpu_samples,
        )
        assert isinstance(_cpu_samples, collections.deque)
        assert _cpu_samples.maxlen == 3


class TestGpuContentionPerProcess:
    """FR-4a: Per-process GPU contention (NVIDIA only)."""

    def test_excludes_own_pid(self, mocker):
        """Own Blender PID is excluded from detection."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "1234, 500\n"
        mocker.patch(f'{MODULE}.subprocess.run', return_value=mock_result)
        mocker.patch(f'{MODULE}.sys.platform', 'linux')
        from sethlans_worker_agent.idle_detection.contention import (
            check_gpu_contention_per_process,
        )
        assert check_gpu_contention_per_process(exclude_pid=1234) is False

    def test_detects_other_process(self, mocker):
        """Non-excluded PID detected -> True."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "5678, 500\n"
        mocker.patch(f'{MODULE}.subprocess.run', return_value=mock_result)
        mocker.patch(f'{MODULE}.sys.platform', 'linux')
        from sethlans_worker_agent.idle_detection.contention import (
            check_gpu_contention_per_process,
        )
        assert check_gpu_contention_per_process(exclude_pid=1234) is True

    def test_nvidia_unavailable_returns_false(self, mocker):
        mocker.patch(
            f'{MODULE}.subprocess.run', side_effect=FileNotFoundError,
        )
        mocker.patch(f'{MODULE}.sys.platform', 'linux')
        from sethlans_worker_agent.idle_detection.contention import (
            check_gpu_contention_per_process,
        )
        assert check_gpu_contention_per_process() is False

    def test_macos_returns_false(self, mocker):
        mocker.patch(f'{MODULE}.sys.platform', 'darwin')
        from sethlans_worker_agent.idle_detection.contention import (
            check_gpu_contention_per_process,
        )
        assert check_gpu_contention_per_process() is False
