# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The job's own card stop during a launch ends the job like a shutdown (GitHub issue #289, C-24 m).

``JobRun.cause`` is the process runner's cancel callable, so a card stop that lands while a launch runs
comes back as the outcome reason ``worker_hardware_changed`` (``blender_job_stop.WORKER_HARDWARE_CHANGED``).
``PhaseRunner.launch`` raises ``Cancelled`` for it, as for every ``CANCEL_CAUSES`` reason, so the job is
released instead of failing the render check with "Unreal render failed (exit code None,
worker_hardware_changed)". Every other reason still reaches the phase's own check as before.
"""
from types import SimpleNamespace

import pytest

from sethlans_worker_agent.blender_job_stop import WORKER_HARDWARE_CHANGED
from sethlans_worker_agent.unreal_executor import phases, render_check
from sethlans_worker_agent.unreal_executor.outcomes import Cancelled, JobFailure
from sethlans_worker_agent.unreal_executor.runner_types import CANCEL_CAUSES
from tests.unit.worker.unreal._executor_fakes import outcome
from tests.unit.worker.unreal.test_phases_and_recovery import Sampler, runner


def _zen_calls(r):
    """Wrap the runner's Zen check to count it: a stopped launch never reaches FR-UE-26's verification."""
    calls, verify = [], r.verify_zen

    def counted(*args, **kwargs):
        calls.append(args)
        return verify(*args, **kwargs)
    r.verify_zen = counted
    return calls


class TestCardStopIsACancel:

    def test_the_reason_is_the_shared_worker_stop_reason(self):
        assert WORKER_HARDWARE_CHANGED == 'worker_hardware_changed'
        assert phases.WORKER_HARDWARE_CHANGED is WORKER_HARDWARE_CHANGED
        assert WORKER_HARDWARE_CHANGED not in CANCEL_CAUSES   # the fix does not widen the cancel watcher's causes

    @pytest.mark.parametrize('phase', ['prep', 'warmup', 'render'])
    def test_a_card_stop_mid_launch_raises_cancelled(self, phase):
        """#289: the render case ended as ERROR; every phase now ends the job the same way."""
        r, calls = runner(outcome(reason=WORKER_HARDWARE_CHANGED, exit_code=None))
        zen = _zen_calls(r)
        with pytest.raises(Cancelled) as info:
            r.launch(phase, 1, SimpleNamespace())
        assert info.value.cause == WORKER_HARDWARE_CHANGED
        assert str(info.value) == 'Unreal job stopped (worker_hardware_changed).'
        assert len(calls['specs']) == 1 and [run.phase for run in r.runs] == [phase]  # the launch ran
        assert zen == [] and calls['disable'] == []

    def test_a_sampled_render_still_takes_its_post_exit_reading(self):
        """The stop is raised after the post-exit reading, like the other cancel causes."""
        sampler = Sampler()
        r, _ = runner(outcome(reason=WORKER_HARDWARE_CHANGED, exit_code=None), sampler=sampler)
        with pytest.raises(Cancelled):
            r.launch('render', 1, SimpleNamespace(), sample=True)
        assert sampler.calls == 2 and r.runs[0].pre == {0: 1001} and r.runs[0].post == {0: 1002}

    def test_survivors_still_win_over_the_card_stop(self):
        """Processes left behind keep their own outcome: the slot is not safe to reuse."""
        from sethlans_worker_agent.unreal_executor.outcomes import SurvivorsLeft
        r, _ = runner(outcome(reason=WORKER_HARDWARE_CHANGED, exit_code=None, survivors=((7, 'UnrealEditor-Cmd.exe'),)))
        with pytest.raises(SurvivorsLeft):
            r.launch('render', 1, SimpleNamespace())

    def test_without_the_fix_the_render_check_would_fail_the_job(self):
        """The bug's path: the render check turns the reason into the ERROR text the issue quotes."""
        stopped = outcome(reason=WORKER_HARDWARE_CHANGED, exit_code=None)
        with pytest.raises(JobFailure, match=r'^Unreal render failed \(exit code None, worker_hardware_changed\)\.$'):
            render_check.check_render(stopped, 'render.1.log', 1, 10)


class TestOtherReasonsUnchanged:

    @pytest.mark.parametrize('cause', CANCEL_CAUSES)
    def test_every_cancel_cause_still_raises_cancelled(self, cause):
        r, calls = runner(outcome(reason=cause, exit_code=None))
        zen = _zen_calls(r)
        with pytest.raises(Cancelled) as info:
            r.launch('render', 1, SimpleNamespace())
        assert info.value.cause == cause and len(calls['specs']) == 1 and zen == []

    def test_the_cancel_causes_are_the_three_known_ones(self):
        assert CANCEL_CAUSES == ('canceled', 'shutdown', 'reassigned')

    @pytest.mark.parametrize('reason,detail,message', [
        ('timeout', '', r'^Unreal render timed out after 30 s\.$'),
        ('hang', 'log silent for 900 s', r'^Unreal render hung \(log silent for 900 s\)\.$'),
        ('fatal', 'Fatal error!', r'^Unreal render hit a fatal log line \(critical_error\): Fatal error!$'),
    ])
    def test_a_failed_render_returns_and_its_check_fails_the_job(self, reason, detail, message):
        r, _ = runner(outcome(reason=reason, exit_code=None, detail=detail, fatal_rule='critical_error'))
        zen = _zen_calls(r)
        run = r.launch('render', 1, SimpleNamespace())
        assert run.outcome.reason == reason and r.runs == [run] and len(zen) == 1
        with pytest.raises(JobFailure, match=message):
            render_check.check_render(run.outcome, run.log_path, 1, 10)

    @pytest.mark.parametrize('reason', ['exited', 'exited_after_grace'])
    def test_a_finished_launch_returns_its_run(self, reason):
        r, _ = runner(outcome(reason=reason))
        zen = _zen_calls(r)
        run = r.launch('warmup', 1, SimpleNamespace())
        assert run.outcome.reason == reason and run.zen_ok and len(zen) == 1

    @pytest.mark.parametrize('reason', ['worker_shutdown', 'worker_hardware_changed '])
    def test_only_the_exact_card_stop_reason_is_a_cancel(self, reason):
        """A near miss (the Blender shutdown reason, a padded string) is not treated as the card stop."""
        r, _ = runner(outcome(reason=reason, exit_code=None))
        assert r.launch('render', 1, SimpleNamespace()).outcome.reason == reason
