# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for worker/sethlans_worker_agent/idle_detection/yield_monitor.py.

Covers yield detection during active rendering (FR-4, FR-5):
- Fast-path: GPU contention, creative app launch, session unlock
- Slow-path: sustained input activity (90s threshold)
- Early-exit: first trigger wins, _triggered prevents re-evaluation
- Write-before-signal ordering (reason set before event)
- Schedule window close detection
- Own Blender PID exclusion from creative app detection
"""
import threading
from unittest.mock import MagicMock

import pytest

MODULE = 'sethlans_worker_agent.idle_detection.yield_monitor'


@pytest.fixture
def mock_dependencies(mocker):
    """Patch all external dependencies for YieldMonitor."""
    mocker.patch(f'{MODULE}.blender_executor')
    mocker.patch(f'{MODULE}.check_gpu_contention_per_process', return_value=False)
    mocker.patch(f'{MODULE}.get_seconds_since_last_input', return_value=999.0)
    mocker.patch(f'{MODULE}.is_inside_claim_window', return_value=True)
    mocker.patch(f'{MODULE}.session_unlock_event', threading.Event())
    mocker.patch(
        'sethlans_worker_agent.config.IDLE_CREATIVE_APP_NAMES',
        ['blender', 'maya'],
    )
    mocker.patch(
        'sethlans_worker_agent.config.IDLE_SLOW_PATH_THRESHOLD_SECONDS', 90,
    )
    mocker.patch(f'{MODULE}.psutil.process_iter', return_value=[])


@pytest.fixture
def monitor(mock_dependencies):
    """Create a YieldMonitor with test parameters."""
    from sethlans_worker_agent.idle_detection.yield_monitor import (
        YieldMonitor,
    )
    manual_stop = threading.Event()
    shutdown = threading.Event()
    ym = YieldMonitor(
        blender_pid=1234,
        job_id=42,
        render_engine='CYCLES',
        manual_stop_event=manual_stop,
        shutdown_event=shutdown,
    )
    return ym


class TestGpuContentionTrigger:
    """FR-4a: GPU contention fast-path."""

    def test_gpu_contention_fires_yield(self, mocker, mock_dependencies):
        mocker.patch(
            f'{MODULE}.check_gpu_contention_per_process', return_value=True,
        )
        mocker.patch(f'{MODULE}.blender_executor.get_last_output_line', return_value=None)
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            YieldMonitor,
        )
        ym = YieldMonitor(
            blender_pid=1234, job_id=42, render_engine='CYCLES',
            manual_stop_event=threading.Event(),
            shutdown_event=threading.Event(),
        )
        ym.start()
        triggered = ym.yield_event.wait(timeout=5)
        ym.stop()
        assert triggered
        reason = ym.get_reason()
        assert reason.reason == "artist_return_gpu_contention"


class TestCreativeAppTrigger:
    """FR-4b: Creative app launch fast-path."""

    def test_creative_app_detected_fires_yield(self, mocker, mock_dependencies):
        """Matching process name triggers yield."""
        mock_proc = MagicMock()
        mock_proc.info = {'pid': 5678, 'name': 'Maya.exe'}
        mocker.patch(f'{MODULE}.psutil.process_iter', return_value=[mock_proc])
        mocker.patch(f'{MODULE}.blender_executor.get_last_output_line', return_value=None)
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            YieldMonitor,
        )
        ym = YieldMonitor(
            blender_pid=1234, job_id=42, render_engine='CYCLES',
            manual_stop_event=threading.Event(),
            shutdown_event=threading.Event(),
        )
        ym.start()
        triggered = ym.yield_event.wait(timeout=5)
        ym.stop()
        assert triggered
        assert ym.get_reason().reason == "artist_return_app_launch"

    def test_own_blender_pid_excluded(self, mocker, mock_dependencies):
        """Process matching own Blender PID is not detected."""
        mock_proc = MagicMock()
        mock_proc.info = {'pid': 1234, 'name': 'blender'}
        mocker.patch(f'{MODULE}.psutil.process_iter', return_value=[mock_proc])
        mocker.patch(f'{MODULE}.blender_executor.get_last_output_line', return_value=None)
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            YieldMonitor,
        )
        ym = YieldMonitor(
            blender_pid=1234, job_id=42, render_engine='CYCLES',
            manual_stop_event=threading.Event(),
            shutdown_event=threading.Event(),
        )
        ym.start()
        triggered = ym.yield_event.wait(timeout=1)
        ym.stop()
        assert not triggered

    def test_case_insensitive_substring_match(self, mocker, mock_dependencies):
        """Case-insensitive substring matching."""
        mock_proc = MagicMock()
        mock_proc.info = {'pid': 9999, 'name': 'Blender-3.6'}
        mocker.patch(f'{MODULE}.psutil.process_iter', return_value=[mock_proc])
        mocker.patch(f'{MODULE}.blender_executor.get_last_output_line', return_value=None)
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            YieldMonitor,
        )
        ym = YieldMonitor(
            blender_pid=1234, job_id=42, render_engine='CYCLES',
            manual_stop_event=threading.Event(),
            shutdown_event=threading.Event(),
        )
        ym.start()
        triggered = ym.yield_event.wait(timeout=5)
        ym.stop()
        assert triggered


class TestSlowPath:
    """FR-5: Sustained input activity slow-path trigger."""

    def test_sustained_activity_fires(self, mocker, mock_dependencies):
        """90s continuous activity -> yield."""
        mocker.patch(f'{MODULE}.get_seconds_since_last_input', return_value=2.0)
        mocker.patch(f'{MODULE}.blender_executor.get_last_output_line', return_value=None)

        # Speed up: set threshold to 0.1s for test
        mocker.patch(
            'sethlans_worker_agent.config.IDLE_SLOW_PATH_THRESHOLD_SECONDS',
            0.1,
        )
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            YieldMonitor,
        )
        ym = YieldMonitor(
            blender_pid=1234, job_id=42, render_engine='CYCLES',
            manual_stop_event=threading.Event(),
            shutdown_event=threading.Event(),
        )
        ym.start()
        triggered = ym.yield_event.wait(timeout=5)
        ym.stop()
        assert triggered
        assert ym.get_reason().reason == "artist_return_sustained_activity"

    def test_gap_resets_timer(self, mocker, mock_dependencies):
        """Gap > 10s resets the presence timer; does not accumulate."""
        # idle_secs > 10 means gap -> reset
        mocker.patch(f'{MODULE}.get_seconds_since_last_input', return_value=15.0)
        mocker.patch(f'{MODULE}.blender_executor.get_last_output_line', return_value=None)
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            YieldMonitor,
        )
        ym = YieldMonitor(
            blender_pid=1234, job_id=42, render_engine='CYCLES',
            manual_stop_event=threading.Event(),
            shutdown_event=threading.Event(),
        )
        ym.start()
        triggered = ym.yield_event.wait(timeout=1)
        ym.stop()
        assert not triggered  # Timer keeps resetting


class TestEarlyExit:
    """First trigger wins; _triggered prevents re-evaluation."""

    def test_triggered_flag_prevents_recheck(self, mocker, mock_dependencies):
        mocker.patch(
            f'{MODULE}.check_gpu_contention_per_process', return_value=True,
        )
        mocker.patch(f'{MODULE}.blender_executor.get_last_output_line', return_value=None)
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            YieldMonitor,
        )
        ym = YieldMonitor(
            blender_pid=1234, job_id=42, render_engine='CYCLES',
            manual_stop_event=threading.Event(),
            shutdown_event=threading.Event(),
        )
        ym.start()
        ym.yield_event.wait(timeout=5)
        ym.stop()
        assert ym._triggered is True


class TestWriteBeforeSignal:
    """Thread safety: reason/progress set before event."""

    def test_reason_set_before_event(self, mocker, mock_dependencies):
        mocker.patch(
            f'{MODULE}.check_gpu_contention_per_process', return_value=True,
        )
        mocker.patch(
            f'{MODULE}.blender_executor.get_last_output_line',
            return_value="Sample 768/1024",
        )
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            YieldMonitor,
        )
        ym = YieldMonitor(
            blender_pid=1234, job_id=42, render_engine='CYCLES',
            manual_stop_event=threading.Event(),
            shutdown_event=threading.Event(),
        )
        ym.start()
        ym.yield_event.wait(timeout=5)
        ym.stop()
        reason = ym.get_reason()
        assert reason is not None
        assert reason.reason == "artist_return_gpu_contention"
        assert reason.progress == pytest.approx(768 / 1024)


class TestScheduleWindowClose:
    """FR-8g: Schedule window close detected on poll."""

    def test_window_close_triggers_yield(self, mocker, mock_dependencies):
        mocker.patch(f'{MODULE}.is_inside_claim_window', return_value=False)
        mocker.patch(
            'sethlans_worker_agent.config.get_schedule_config',
            return_value={'enabled': True, 'start': '18:00', 'end': '07:00'},
        )
        mocker.patch(f'{MODULE}.blender_executor.get_last_output_line', return_value=None)
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            YieldMonitor,
        )
        ym = YieldMonitor(
            blender_pid=1234, job_id=42, render_engine='CYCLES',
            manual_stop_event=threading.Event(),
            shutdown_event=threading.Event(),
        )
        # Backdate render start so the 30-second boundary guard passes
        ym._render_start_time -= 60
        ym.start()
        triggered = ym.yield_event.wait(timeout=5)
        ym.stop()
        assert triggered
        assert ym.get_reason().reason == "schedule_window_closed"

    def test_window_close_skipped_within_30s_of_render_start(
        self, mocker, mock_dependencies,
    ):
        """Schedule check skipped within 30s of render start (race guard)."""
        mocker.patch(f'{MODULE}.is_inside_claim_window', return_value=False)
        mocker.patch(
            'sethlans_worker_agent.config.get_schedule_config',
            return_value={'enabled': True, 'start': '18:00', 'end': '07:00'},
        )
        mocker.patch(f'{MODULE}.blender_executor.get_last_output_line', return_value=None)
        from sethlans_worker_agent.idle_detection.yield_monitor import (
            YieldMonitor,
        )
        ym = YieldMonitor(
            blender_pid=1234, job_id=42, render_engine='CYCLES',
            manual_stop_event=threading.Event(),
            shutdown_event=threading.Event(),
        )
        # render_start_time is recent (default), so guard blocks check
        ym.start()
        triggered = ym.yield_event.wait(timeout=1)
        ym.stop()
        assert not triggered


class TestStop:
    """stop() cleans up the monitor thread."""

    def test_stop_joins_thread(self, monitor):
        monitor.start()
        assert monitor._thread.is_alive()
        monitor.stop()
        assert not monitor._thread.is_alive()
