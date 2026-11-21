# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""UE process runner with a fake Win32 job, Popen and psutil: the launch (FR-UE-11; AC-41, FR-T-5)."""
import pytest

from sethlans_worker_agent.unreal_executor import process_runner, user_state
from sethlans_worker_agent.unreal_executor.runner_types import UNCONFINED_MESSAGE, UnconfinedLaunchError

from ._process_runner_world import World
from ._runner_fakes import make_launch


@pytest.fixture()
def world(tmp_path):
    return World(tmp_path)


def test_exit_is_confirmed_and_the_process_was_suspended_assigned_resumed(world):
    world.steps[3] = lambda: setattr(world.root, 'alive', False)
    outcome = world.run()
    assert outcome.reason == 'exited' and outcome.exit_code == 0 and outcome.survivors == ()
    assert world.api.calls[:3] == ['create', 'set', 'assign'] and world.root.resumed
    flags = world.popen.calls[0][1]['creationflags']
    assert flags & process_runner.CREATE_SUSPENDED
    assert world.popen.calls[0][1]['shell'] is False and 'stdout' in world.popen.calls[0][1]
    assert 'terminate' in world.api.calls and world.api.calls[-1] == 'close'
    assert outcome.job_object == 'ok'


def test_create_failure_refuses_to_launch(world):
    world.api.fail.add('create')
    with pytest.raises(UnconfinedLaunchError, match='refusing to run unconfined'):
        world.run()
    assert world.popen.calls == []


def test_assignment_failure_kills_the_process(world):
    world.api.fail.add('assign')
    with pytest.raises(UnconfinedLaunchError) as info:
        world.run()
    assert str(info.value) == UNCONFINED_MESSAGE
    assert world.root.killed and not world.root.resumed and 'close' in world.api.calls


def test_environment_without_isolation_is_refused(world, tmp_path):
    spec = world.spec(launch=make_launch(tmp_path, env={'PATH': 'x'}))
    with pytest.raises(user_state.IsolationError):
        world.run(spec)
    assert world.popen.calls == [] and world.api.calls == []
