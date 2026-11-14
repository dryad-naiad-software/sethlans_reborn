# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real Job Objects and the UE process runner with harmless Python children (FR-UE-11, FR-UE-27; no Unreal)."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import psutil
import pytest

from sethlans_worker_agent.unreal_executor import command, process_runner, user_state
from sethlans_worker_agent.unreal_executor.job_object import JobObject
from sethlans_worker_agent.unreal_executor.runner_types import MB, WARMUP_SUCCESS, RunSpec

from ._children import WITH_GRANDCHILD, alive, kill_quietly, python_args, wait_until

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows Job Objects')
REPO = Path(__file__).resolve().parents[4]


def test_kill_on_job_close_kills_child_and_grandchild():
    job = JobObject.create()
    proc = subprocess.Popen(python_args(WITH_GRANDCHILD.format(seconds=60)),
                            creationflags=process_runner.CREATE_SUSPENDED | subprocess.CREATE_NO_WINDOW)
    try:
        job.assign(int(proc._handle))
        psutil.Process(proc.pid).resume()
        assert wait_until(lambda: len(job.pids()) >= 2)
        members = job.pids()
        assert proc.pid in members and job.active_processes() == len(members)
        job.close()  # the worker dying closes the handle the same way
        assert wait_until(lambda: not any(alive(pid) for pid in members))
    finally:
        job.close()
        kill_quietly(proc)


def test_worker_death_kills_the_job(tmp_path):
    """A 'worker' that holds the job and dies without cleanup takes its UE tree with it."""
    pid_file = tmp_path / 'pids.json'
    worker = textwrap.dedent(f"""
        import json, os, subprocess, sys, time
        import psutil
        sys.path[:0] = {[str(REPO / 'worker'), str(REPO)]!r}
        from sethlans_worker_agent.unreal_executor.job_object import JobObject
        job = JobObject.create()
        child = subprocess.Popen({python_args(WITH_GRANDCHILD.format(seconds=60))!r}, creationflags=0x4 | 0x08000000)
        job.assign(int(child._handle))
        psutil.Process(child.pid).resume()
        while len(job.pids()) < 2:
            time.sleep(0.1)
        open({str(pid_file)!r}, 'w').write(json.dumps(job.pids()))
        os._exit(0)
    """)
    subprocess.run([sys.executable, '-c', worker], timeout=60, check=True)
    members = json.loads(pid_file.read_text())
    assert members and wait_until(lambda: not any(alive(pid) for pid in members))


@pytest.fixture()
def profile(tmp_path):
    path = tmp_path / 'cache' / 'ueprofile'
    path.parent.mkdir()
    user_state.ensure_profile(path)
    return path


def launch_for(profile, args, phase='render'):
    from sethlans_worker_agent import env_cleanup
    env = env_cleanup.clean_env_for_unreal(None)
    env.update(user_state.isolation_env(str(profile)))
    return command.UeLaunch(phase=phase, pilot=False, args=tuple(args), env=env, cwd=str(profile.parent),
                            profile=str(profile))


def spec_for(tmp_path, launch, **overrides):
    values = dict(launch=launch, log_path=tmp_path / 'phase.log', stdout_path=tmp_path / 'phase.stdout.txt',
                  ledger_path=tmp_path / 'procs.json', timeout_s=60, hang_s=900, quit_grace_s=2,
                  log_max_bytes=512 * MB, log_flood_bytes_per_min=100 * MB)
    values.update(overrides)
    return RunSpec(**values)


def child_writing(log, lines_code):
    return python_args(textwrap.dedent(f"""
        import os, subprocess, sys, time
        log = open({str(log)!r}, 'a', encoding='utf-8', buffering=1)
        {lines_code}
    """))


def test_runner_quit_grace_terminates_the_whole_tree(tmp_path, profile):
    log = tmp_path / 'phase.log'
    code = ("subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(1); "
            "log.write('[Warm] done, quitting\\n'); time.sleep(60)")
    spec = spec_for(tmp_path, launch_for(profile, child_writing(log, code), 'warmup'), success_marker=WARMUP_SUCCESS)
    outcome = process_runner.run(spec, process_runner.RunnerRegistry())
    assert outcome.reason == 'exited_after_grace' and outcome.quit_forced and outcome.survivors == ()
    ledger = json.loads((tmp_path / 'procs.json').read_text())['procs']
    assert len(ledger) >= 2  # the root and its grandchild (plus a venv launcher hop)
    assert not any(alive(entry['pid']) for entry in ledger)


def test_runner_child_sees_the_isolation_environment(tmp_path, profile):
    log = tmp_path / 'phase.log'
    code = "log.write('USERPROFILE=' + os.environ['USERPROFILE'] + '\\nTEMP=' + os.environ['TEMP'] + '\\n')"
    spec = spec_for(tmp_path, launch_for(profile, child_writing(log, code)))
    outcome = process_runner.run(spec, process_runner.RunnerRegistry())
    assert outcome.reason == 'exited' and outcome.exit_code == 0
    text = log.read_text()
    assert f"USERPROFILE={profile / 'Profile'}" in text and f"TEMP={profile / 'Temp'}" in text


def test_known_folder_probe_through_the_runner_resolves_into_the_farm_profile(tmp_path, profile):
    spec = spec_for(tmp_path, launch_for(profile, user_state.probe_command()))
    outcome = process_runner.run(spec, process_runner.RunnerRegistry())
    assert outcome.reason == 'exited' and outcome.survivors == ()
    stdout = (tmp_path / 'phase.stdout.txt').read_text()
    result = user_state.evaluate_probe_output(stdout, str(profile), lambda: 0.0)
    assert result.ok, result.reason
    assert user_state.check_known_folders(str(profile)).ok


def test_known_folder_check_fails_when_the_profile_folders_are_missing(tmp_path):
    bare = tmp_path / 'bare'
    bare.mkdir()
    result = user_state.check_known_folders(str(bare))
    assert not result.ok and 'shell folders are redirected' in result.reason


def test_runner_log_flood_is_fatal(tmp_path, profile):
    log = tmp_path / 'phase.log'
    code = "log.write(('x' * 1023 + '\\n') * (20 * 1024)); log.flush(); time.sleep(60)"
    spec = spec_for(tmp_path, launch_for(profile, child_writing(log, code)), log_max_bytes=16 * MB)
    outcome = process_runner.run(spec, process_runner.RunnerRegistry())
    assert outcome.reason == 'fatal' and outcome.fatal_rule == 'log_flood' and outcome.survivors == ()


def test_runner_hang_on_a_silent_idle_child(tmp_path, profile, monkeypatch):
    from sethlans_worker_agent.unreal_executor import runner_state
    monkeypatch.setattr(runner_state, 'HANG_CPU_WINDOW_SECONDS', 3.0)  # the real window is 60 s
    log = tmp_path / 'phase.log'
    spec = spec_for(tmp_path, launch_for(profile, child_writing(log, "time.sleep(60)")), hang_s=3)
    outcome = process_runner.run(spec, process_runner.RunnerRegistry())
    assert outcome.reason == 'hang' and outcome.survivors == ()


def test_runner_refuses_an_environment_without_isolation(tmp_path, profile):
    launch = launch_for(profile, python_args('pass'))
    bad = command.UeLaunch(phase='render', pilot=False, args=launch.args, env=dict(os.environ), cwd=launch.cwd,
                           profile=launch.profile)
    with pytest.raises(user_state.IsolationError):
        process_runner.run(spec_for(tmp_path, bad), process_runner.RunnerRegistry())
