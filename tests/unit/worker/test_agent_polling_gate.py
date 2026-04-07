# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the agent main loop polling gate (FR-30).

When WorkerCapacity.is_full() returns True, agent._run_loop_iteration
must NOT call api_handler.poll_for_available_jobs. Heartbeats continue
normally.
"""
import sys

# The agent module does argparse at import time, which would consume
# pytest's argv. Stub argv with a harmless command line before importing.
_saved_argv = sys.argv
sys.argv = ['agent-test-runner']
try:
    from sethlans_worker_agent import agent as agent_module  # noqa: E402
finally:
    sys.argv = _saved_argv


class TestPollingGate:

    def _patch_common(self, mocker):
        """Shared patches for the agent main-loop iteration tests."""
        mocker.patch(
            'sethlans_worker_agent.job_processor.get_active_jobs_snapshot',
            return_value={},
        )
        mocker.patch(
            'sethlans_worker_agent.system_monitor.send_heartbeat'
        )
        mocker.patch(
            'sethlans_worker_agent.job_processor.maybe_assert_gpu_count_unchanged'
        )
        mocker.patch(
            'sethlans_worker_agent.version_sync.process_pending_downloads'
        )
        mocker.patch(
            'sethlans_worker_agent.version_sync.process_pending_removals'
        )
        mocker.patch(
            'sethlans_worker_agent.agent._should_skip_polling',
            return_value=None,
        )
        # Short-circuit the wait so the test returns immediately.
        mocker.patch.object(agent_module._shutdown_event, 'wait')

    def test_polling_skipped_when_capacity_full(self, mocker):
        self._patch_common(mocker)
        mocker.patch(
            'sethlans_worker_agent.job_processor.capacity_is_full',
            return_value=True,
        )
        poll_spy = mocker.patch(
            'sethlans_worker_agent.api_handler.poll_for_available_jobs'
        )
        claim_spy = mocker.patch(
            'sethlans_worker_agent.job_processor.get_and_claim_job'
        )
        heartbeat_spy = mocker.patch(
            'sethlans_worker_agent.system_monitor.send_heartbeat'
        )

        agent_module._run_loop_iteration(worker_id=1)

        # Heartbeat IS called (FR-7).
        heartbeat_spy.assert_called_once()
        # Polling and claim are NOT called.
        poll_spy.assert_not_called()
        claim_spy.assert_not_called()

    def test_polling_runs_when_capacity_free(self, mocker):
        self._patch_common(mocker)
        mocker.patch(
            'sethlans_worker_agent.job_processor.capacity_is_full',
            return_value=False,
        )
        claim_spy = mocker.patch(
            'sethlans_worker_agent.job_processor.get_and_claim_job',
            return_value=None,
        )
        heartbeat_spy = mocker.patch(
            'sethlans_worker_agent.system_monitor.send_heartbeat'
        )

        agent_module._run_loop_iteration(worker_id=1)

        heartbeat_spy.assert_called_once()
        claim_spy.assert_called_once_with(1)

    def test_skip_polling_short_circuits_capacity_gate(self, mocker):
        """If _should_skip_polling returns a value, the capacity gate is
        never consulted for this iteration."""
        self._patch_common(mocker)
        mocker.patch(
            'sethlans_worker_agent.agent._should_skip_polling',
            return_value=5,
        )
        cap_spy = mocker.patch(
            'sethlans_worker_agent.job_processor.capacity_is_full',
            return_value=False,
        )
        claim_spy = mocker.patch(
            'sethlans_worker_agent.job_processor.get_and_claim_job'
        )
        agent_module._run_loop_iteration(worker_id=1)
        cap_spy.assert_not_called()
        claim_spy.assert_not_called()
