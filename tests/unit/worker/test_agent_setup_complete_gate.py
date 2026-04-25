# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the agent main-loop manager-setup-complete gate (issue #126).

When ``check_manager_setup_complete()`` returns False, ``_run_loop_iteration``
must skip ``process_pending_downloads()``, ``process_pending_removals()``,
the polling-skip checks, the capacity check, and ``get_and_claim_job()`` —
heartbeats still fire each iteration. The gate is independent of ``is_busy``.
"""
import sys

# The agent module does argparse at import time, which would consume
# pytest's argv. Stub argv before importing.
_saved_argv = sys.argv
sys.argv = ['agent-test-runner']
try:
    from sethlans_worker_agent import agent as agent_module  # noqa: E402
finally:
    sys.argv = _saved_argv

from sethlans_worker_agent import agent_setup, system_monitor  # noqa: E402


def _patch_iteration_common(mocker, active_jobs=None):
    """Shared mocks for _run_loop_iteration tests."""
    mocker.patch(
        'sethlans_worker_agent.job_processor.get_active_jobs_snapshot',
        return_value=active_jobs if active_jobs is not None else {},
    )
    mocker.patch(
        'sethlans_worker_agent.job_processor.maybe_assert_gpu_count_unchanged'
    )
    # Short-circuit waits so tests return immediately.
    mocker.patch.object(agent_module._shutdown_event, 'wait')
    # Reset transition tracking so check_manager_setup_complete behaves
    # deterministically across tests.
    agent_setup._last_known_setup_complete = None


class TestRunLoopIterationGate:

    def test_gate_false_skips_downloads_and_claim(self, mocker):
        _patch_iteration_common(mocker)
        mocker.patch.object(
            system_monitor, '_manager_setup_complete', False,
        )
        # Make is_manager_setup_complete return False directly to be
        # robust against attribute-vs-function patching.
        mocker.patch(
            'sethlans_worker_agent.system_monitor.is_manager_setup_complete',
            return_value=False,
        )
        heartbeat_spy = mocker.patch(
            'sethlans_worker_agent.system_monitor.send_heartbeat'
        )
        downloads_spy = mocker.patch(
            'sethlans_worker_agent.version_sync.process_pending_downloads'
        )
        removals_spy = mocker.patch(
            'sethlans_worker_agent.version_sync.process_pending_removals'
        )
        claim_spy = mocker.patch(
            'sethlans_worker_agent.job_processor.get_and_claim_job'
        )
        cap_spy = mocker.patch(
            'sethlans_worker_agent.job_processor.capacity_is_full'
        )

        agent_module._run_loop_iteration(worker_id=1)

        heartbeat_spy.assert_called_once()
        downloads_spy.assert_not_called()
        removals_spy.assert_not_called()
        claim_spy.assert_not_called()
        cap_spy.assert_not_called()

    def test_gate_true_runs_full_path(self, mocker):
        _patch_iteration_common(mocker)
        mocker.patch(
            'sethlans_worker_agent.system_monitor.is_manager_setup_complete',
            return_value=True,
        )
        mocker.patch(
            'sethlans_worker_agent.agent._should_skip_polling',
            return_value=None,
        )
        mocker.patch(
            'sethlans_worker_agent.job_processor.capacity_is_full',
            return_value=False,
        )
        heartbeat_spy = mocker.patch(
            'sethlans_worker_agent.system_monitor.send_heartbeat'
        )
        downloads_spy = mocker.patch(
            'sethlans_worker_agent.version_sync.process_pending_downloads'
        )
        removals_spy = mocker.patch(
            'sethlans_worker_agent.version_sync.process_pending_removals'
        )
        claim_spy = mocker.patch(
            'sethlans_worker_agent.job_processor.get_and_claim_job',
            return_value=None,
        )

        agent_module._run_loop_iteration(worker_id=1)

        heartbeat_spy.assert_called_once()
        downloads_spy.assert_called_once()
        removals_spy.assert_called_once()
        claim_spy.assert_called_once_with(1)

    def test_gate_false_with_busy_worker_still_skips_claim(self, mocker):
        """The gate is independent of is_busy — even with active jobs the
        worker must still skip the claim path when setup is not complete.
        (Downloads are already gated by ``if not is_busy`` in the loop, so
        only the claim path is meaningful to assert here when busy.)

        ``_should_skip_polling`` and ``capacity_is_full`` are mocked to
        return values that would normally allow the claim to proceed —
        proving the gate (not some other guard) is what blocks the claim.
        """
        _patch_iteration_common(
            mocker, active_jobs={42: object()},
        )
        mocker.patch(
            'sethlans_worker_agent.system_monitor.is_manager_setup_complete',
            return_value=False,
        )
        mocker.patch(
            'sethlans_worker_agent.agent._should_skip_polling',
            return_value=None,
        )
        mocker.patch(
            'sethlans_worker_agent.job_processor.capacity_is_full',
            return_value=False,
        )
        heartbeat_spy = mocker.patch(
            'sethlans_worker_agent.system_monitor.send_heartbeat'
        )
        claim_spy = mocker.patch(
            'sethlans_worker_agent.job_processor.get_and_claim_job'
        )

        agent_module._run_loop_iteration(worker_id=1)

        heartbeat_spy.assert_called_once()
        claim_spy.assert_not_called()
