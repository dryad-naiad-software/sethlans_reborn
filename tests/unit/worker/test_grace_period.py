# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for worker/sethlans_worker_agent/blender_yield.py.

Covers grace period and abort logic (FR-6):
- should_abort() with manual_stop, job canceled, shutdown
- Cycles >= 75% allows finish; Cycles < 75% immediate abort
- Eevee/Workbench always allows finish
- 95% progress abort logs warning
- handle_yield orchestration
"""
import threading
from unittest.mock import MagicMock

MODULE = 'sethlans_worker_agent.blender_yield'


class TestShouldAbort:
    """FR-6g: _should_abort checks multiple abort conditions."""

    def test_manual_stop_event_set(self, mocker):
        """Manual stop -> True."""
        mocker.patch(f'{MODULE}.api_handler.get_job_status', return_value='RENDERING')
        from sethlans_worker_agent.blender_yield import should_abort
        manual = threading.Event()
        manual.set()
        shutdown = threading.Event()
        assert should_abort(manual, 42, shutdown) is True

    def test_job_canceled(self, mocker):
        """Job status CANCELED -> True."""
        mocker.patch(f'{MODULE}.api_handler.get_job_status', return_value='CANCELED')
        from sethlans_worker_agent.blender_yield import should_abort
        manual = threading.Event()
        shutdown = threading.Event()
        assert should_abort(manual, 42, shutdown) is True

    def test_shutdown_event_set(self, mocker):
        """Shutdown event -> True."""
        mocker.patch(f'{MODULE}.api_handler.get_job_status', return_value='RENDERING')
        from sethlans_worker_agent.blender_yield import should_abort
        manual = threading.Event()
        shutdown = threading.Event()
        shutdown.set()
        assert should_abort(manual, 42, shutdown) is True

    def test_all_clear(self, mocker):
        """No abort conditions -> False."""
        mocker.patch(f'{MODULE}.api_handler.get_job_status', return_value='RENDERING')
        from sethlans_worker_agent.blender_yield import should_abort
        manual = threading.Event()
        shutdown = threading.Event()
        assert should_abort(manual, 42, shutdown) is False

    def test_none_manual_stop_event(self, mocker):
        """manual_stop_event=None does not crash."""
        mocker.patch(f'{MODULE}.api_handler.get_job_status', return_value='RENDERING')
        from sethlans_worker_agent.blender_yield import should_abort
        assert should_abort(None, 42, threading.Event()) is False

    def test_none_shutdown_event(self, mocker):
        """shutdown_event=None does not crash."""
        mocker.patch(f'{MODULE}.api_handler.get_job_status', return_value='RENDERING')
        from sethlans_worker_agent.blender_yield import should_abort
        assert should_abort(threading.Event(), 42, None) is False


class TestHandleYieldCyclesProgress:
    """FR-6b/c: Cycles progress threshold determines grace behavior."""

    def test_cycles_75_percent_allows_finish(self, mocker):
        """Cycles >= 75%: allow finish (enters grace period loop)."""
        mocker.patch(
            'sethlans_worker_agent.idle_detection.progress_parser'
            '.parse_blender_progress',
            return_value=0.80,
        )
        mocker.patch(
            'sethlans_worker_agent.blender_executor.get_last_output_line',
            return_value="Sample 820/1024",
        )
        mocker.patch(
            'sethlans_worker_agent.config.IDLE_GRACE_PERIOD_CAP_SECONDS', 2,
        )
        mock_terminate = mocker.patch(f'{MODULE}.terminate_process_tree')
        mock_yield_monitor = MagicMock()
        mock_yield_monitor.get_reason.return_value = MagicMock(
            reason="artist_return_gpu_contention",
        )
        # poll() is called at: (1) if allow_finish and process.poll()
        # (2) while process.poll() (3) if process.poll() after loop
        # Process exits on the second call inside the while loop
        mock_process = MagicMock()
        mock_process.pid = 9999
        mock_process.poll.side_effect = [None, None, 0, 0]

        mocker.patch(f'{MODULE}.should_abort', return_value=False)
        mocker.patch(
            f'{MODULE}.time.monotonic',
            side_effect=[100.0, 101.0, 101.5],
        )

        from sethlans_worker_agent.blender_yield import handle_yield
        was_yielded, grace_outcome = handle_yield(
            mock_process, 42, 'CYCLES', mock_yield_monitor,
            threading.Event(), threading.Event(),
        )
        assert was_yielded is True
        assert grace_outcome == "finished"
        # Process exited gracefully, no termination needed
        mock_terminate.assert_not_called()

    def test_cycles_below_75_percent_immediate_abort(self, mocker):
        """Cycles < 75%: immediate abort (no grace period)."""
        mocker.patch(
            'sethlans_worker_agent.idle_detection.progress_parser'
            '.parse_blender_progress',
            return_value=0.50,
        )
        mocker.patch(
            'sethlans_worker_agent.blender_executor.get_last_output_line',
            return_value="Sample 512/1024",
        )
        mock_terminate = mocker.patch(f'{MODULE}.terminate_process_tree')
        mock_yield_monitor = MagicMock()
        mock_yield_monitor.get_reason.return_value = MagicMock(
            reason="artist_return_app_launch",
        )
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.pid = 9999

        from sethlans_worker_agent.blender_yield import handle_yield
        was_yielded, grace_outcome = handle_yield(
            mock_process, 42, 'CYCLES', mock_yield_monitor,
            threading.Event(), threading.Event(),
        )
        assert was_yielded is True
        assert grace_outcome == "aborted"
        mock_terminate.assert_called_once_with(9999, 42)


class TestHandleYieldNonCyclesEngines:
    """FR-6b: Eevee/Workbench always allow finish."""

    def test_eevee_always_allows_finish(self, mocker):
        """Eevee: progress indeterminate -> always grace period."""
        mocker.patch(
            'sethlans_worker_agent.idle_detection.progress_parser'
            '.parse_blender_progress',
            return_value=None,
        )
        mocker.patch(
            'sethlans_worker_agent.blender_executor.get_last_output_line',
            return_value="",
        )
        mocker.patch(
            'sethlans_worker_agent.config.IDLE_GRACE_PERIOD_CAP_SECONDS', 2,
        )
        mock_terminate = mocker.patch(f'{MODULE}.terminate_process_tree')
        mock_yield_monitor = MagicMock()
        mock_yield_monitor.get_reason.return_value = MagicMock(
            reason="artist_return_sustained_activity",
        )
        # poll() calls: (1) if allow_finish and process.poll()
        # (2) while process.poll() (3) if process.poll() after loop
        mock_process = MagicMock()
        mock_process.poll.side_effect = [None, None, 0, 0]
        mock_process.pid = 9999

        mocker.patch(f'{MODULE}.should_abort', return_value=False)
        mocker.patch(
            f'{MODULE}.time.monotonic',
            side_effect=[100.0, 101.0, 101.5],
        )

        from sethlans_worker_agent.blender_yield import handle_yield
        was_yielded, grace_outcome = handle_yield(
            mock_process, 42, 'BLENDER_EEVEE_NEXT', mock_yield_monitor,
            threading.Event(), threading.Event(),
        )
        assert was_yielded is True
        assert grace_outcome == "finished"
        mock_terminate.assert_not_called()


class TestGracePeriodCap:
    """FR-6d: Wall-clock cap on grace period."""

    def test_95_percent_abort_logs_warning(self, mocker):
        """Aborting at >= 95% logs a warning about compositing."""
        mocker.patch(
            'sethlans_worker_agent.idle_detection.progress_parser'
            '.parse_blender_progress',
            return_value=0.96,
        )
        mocker.patch(
            'sethlans_worker_agent.blender_executor.get_last_output_line',
            return_value="Sample 983/1024",
        )
        mocker.patch(
            'sethlans_worker_agent.config.IDLE_GRACE_PERIOD_CAP_SECONDS', 0,
        )
        mocker.patch(f'{MODULE}.terminate_process_tree')
        mock_yield_monitor = MagicMock()
        mock_yield_monitor.get_reason.return_value = MagicMock(
            reason="artist_return_gpu_contention",
        )
        mock_process = MagicMock()
        mock_process.poll.return_value = None  # Always running
        mock_process.pid = 9999

        # deadline already passed
        mocker.patch(f'{MODULE}.time.monotonic', return_value=200.0)
        mocker.patch(f'{MODULE}.should_abort', return_value=False)
        mocker.patch(f'{MODULE}.time.sleep')

        mock_logger = mocker.patch(f'{MODULE}.logger')

        from sethlans_worker_agent.blender_yield import handle_yield
        handle_yield(
            mock_process, 42, 'CYCLES', mock_yield_monitor,
            threading.Event(), threading.Event(),
        )
        # Verify warning logged for >= 95% abort
        warning_calls = [
            c for c in mock_logger.warning.call_args_list
            if '95' in str(c) or 'compositing' in str(c).lower()
        ]
        assert len(warning_calls) > 0


class TestTerminateProcessTree:
    """FR-6e: SIGTERM -> 5s -> SIGKILL process tree cleanup."""

    def test_terminate_tree_sends_sigterm(self, mocker):
        mock_parent = MagicMock()
        mock_child = MagicMock()
        mock_parent.children.return_value = [mock_child]
        mocker.patch(f'{MODULE}.psutil.Process', return_value=mock_parent)
        mocker.patch(f'{MODULE}.psutil.wait_procs', return_value=([], []))

        from sethlans_worker_agent.blender_yield import terminate_process_tree
        terminate_process_tree(9999, job_id=42)

        mock_child.terminate.assert_called_once()
        mock_parent.terminate.assert_called_once()

    def test_escalates_to_sigkill_on_survivors(self, mocker):
        mock_parent = MagicMock()
        mock_parent.children.return_value = []
        mocker.patch(f'{MODULE}.psutil.Process', return_value=mock_parent)
        # Some processes survive SIGTERM
        mocker.patch(
            f'{MODULE}.psutil.wait_procs',
            return_value=([], [mock_parent]),
        )
        from sethlans_worker_agent.blender_yield import terminate_process_tree
        terminate_process_tree(9999)
        mock_parent.kill.assert_called_once()

    def test_handles_already_exited_process(self, mocker):
        mocker.patch(
            f'{MODULE}.psutil.Process',
            side_effect=__import__('psutil').NoSuchProcess(pid=9999),
        )
        from sethlans_worker_agent.blender_yield import terminate_process_tree
        # Should not raise
        terminate_process_tree(9999)
