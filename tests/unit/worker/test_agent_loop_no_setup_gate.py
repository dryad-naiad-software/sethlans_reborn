# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Regression tests for issue #304: the worker loop no longer gates on the
manager's "setup complete" state.

Workers used to idle (heartbeat only) while the heartbeat answer said
``manager_setup_complete: False``.  The launcher now starts the manager
only after setup, so the gate is gone: one loop iteration sends the
heartbeat, processes downloads, and polls and claims a job with no
manager setup state anywhere, even if an older manager still sends the
field as False.
"""
import sys

import pytest

# The agent module does argparse at import time, which would consume
# pytest's argv. Stub argv before importing.
_saved_argv = sys.argv
sys.argv = ['agent-test-runner']
try:
    from sethlans_worker_agent import agent as agent_module  # noqa: E402
finally:
    sys.argv = _saved_argv

from sethlans_worker_agent import agent_setup, system_monitor  # noqa: E402


@pytest.fixture
def loop_env(mocker):
    """Patch the loop's collaborators; return the download/claim spies."""
    mocker.patch(
        'sethlans_worker_agent.job_processor.get_active_jobs_snapshot',
        return_value={},
    )
    mocker.patch(
        'sethlans_worker_agent.job_processor.maybe_assert_gpu_list_unchanged'
    )
    mocker.patch(
        'sethlans_worker_agent.agent._should_skip_polling', return_value=None,
    )
    mocker.patch(
        'sethlans_worker_agent.job_processor.capacity_is_full',
        return_value=False,
    )
    mocker.patch(
        'sethlans_worker_agent.version_sync.process_pending_removals'
    )
    # Short-circuit waits so the test returns immediately.
    wait = mocker.patch.object(agent_module._shutdown_event, 'wait')
    downloads = mocker.patch(
        'sethlans_worker_agent.version_sync.process_pending_downloads'
    )
    claim = mocker.patch(
        'sethlans_worker_agent.job_processor.get_and_claim_job',
        return_value=None,
    )
    return downloads, claim, wait


@pytest.fixture
def real_heartbeat(mocker):
    """Run the real ``send_heartbeat`` against a faked manager answer.

    Returns the list of answers the fake manager sends; tests append the
    answer before running the loop.
    """
    mocker.patch.object(system_monitor, 'WORKER_ID', 7)
    mocker.patch.object(system_monitor, '_versions_ready', False)
    mocker.patch.object(
        system_monitor, 'get_system_info', return_value={'hostname': 'w1'},
    )
    mocker.patch.object(system_monitor, '_get_ui_url', return_value='')
    mocker.patch.object(
        system_monitor, '_get_ui_cert_fingerprint', return_value='',
    )
    mocker.patch.object(system_monitor, '_add_unreal_payload')
    mocker.patch.object(system_monitor, '_add_active_job_ids')
    mocker.patch.object(system_monitor.card_payload, 'add_to')
    mocker.patch.object(system_monitor.card_choice, 'apply_manager_dismissals')
    mocker.patch.object(system_monitor.card_remote, 'handle_response')
    mocker.patch.object(
        system_monitor, '_process_heartbeat_versions', return_value=True,
    )
    mocker.patch(
        'sethlans_worker_agent.config.get_schedule_config', return_value={},
    )
    answers = []
    sender = mocker.patch(
        'sethlans_worker_agent.api_handler.send_authenticated_heartbeat',
        side_effect=lambda _payload: answers.pop(0),
    )
    return answers, sender


class TestLoopWithoutSetupGate:

    def test_iteration_downloads_and_claims(self, mocker, loop_env):
        downloads, claim, _wait = loop_env
        heartbeat = mocker.patch(
            'sethlans_worker_agent.system_monitor.send_heartbeat'
        )

        agent_module._run_loop_iteration(worker_id=1)

        heartbeat.assert_called_once_with(is_busy=False, active_jobs={})
        downloads.assert_called_once_with()
        claim.assert_called_once_with(1)

    @pytest.mark.parametrize('answer', [
        {'id': 7, 'required_blender_versions': []},
        # An older manager that still sends the removed field.
        {'id': 7, 'required_blender_versions': [],
         'manager_setup_complete': False},
    ])
    def test_heartbeat_answer_cannot_idle_the_worker(
        self, loop_env, real_heartbeat, answer,
    ):
        downloads, claim, _wait = loop_env
        answers, sender = real_heartbeat
        answers.append(answer)

        agent_module._run_loop_iteration(worker_id=7)

        sender.assert_called_once()
        assert system_monitor.are_versions_ready() is True
        downloads.assert_called_once_with()
        claim.assert_called_once_with(7)

    def test_sent_heartbeat_carries_no_setup_field(
        self, loop_env, real_heartbeat,
    ):
        answers, sender = real_heartbeat
        answers.append({'id': 7})

        agent_module._run_loop_iteration(worker_id=7)

        payload = sender.call_args.args[0]
        assert 'manager_setup_complete' not in payload


class TestGateHelpersRemoved:

    @pytest.mark.parametrize('module, name', [
        (system_monitor, '_manager_setup_complete'),
        (system_monitor, 'is_manager_setup_complete'),
        (system_monitor, '_update_manager_setup_complete'),
        (agent_setup, 'check_manager_setup_complete'),
        (agent_setup, '_last_known_setup_complete'),
    ])
    def test_no_setup_complete_state_left(self, module, name):
        assert not hasattr(module, name)
