# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The runner's RAM figures (render per card FR-PC-RAM-2): every tick publishes the members' summed
private working set and commit into the RAM ledger under the claim's id; the launch keeps its peak
working set; the Job Object's peaks are read after the tree exits and before the handle closes; a
failed read is unknown and marks the ledger entry failed. Fake Job Object, processes and reader.
"""
import pytest

from sethlans_worker_agent.unreal_executor import process_runner
from sethlans_worker_agent.unreal_executor.job_object import JobObject
from sethlans_worker_agent.unreal_executor.ram_ledger import RamLedger
from sethlans_worker_agent.unreal_executor.runner_ram import PhaseRam, RamTarget, RunnerRam
from sethlans_worker_agent.unreal_executor.runner_types import MB, UnconfinedLaunchError
from tests.unit.worker.unreal._process_runner_world import World

from ._runner_fakes import ROOT_PID, Clock, FakePs

SCW = 1001
GB = 1024 * MB


class Watch(RamLedger):
    """A ledger that also records each publish, so a test sees every tick's figures."""

    def __init__(self):
        super().__init__(clock=Clock(500.0))
        self.published, self.failed_marks = [], 0

    def publish(self, claim_id, current_ws, current_commit):
        self.published.append((current_ws, current_commit))
        return super().publish(claim_id, current_ws, current_commit)

    def mark_failed(self, claim_id):
        self.failed_marks += 1
        return super().mark_failed(claim_id)


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path, kids=[FakePs(SCW, exe='C:/UE/Engine/Binaries/Win64/ShaderCompileWorker.exe')])
    w.ledger = Watch()
    w.ledger.register('claim-a', 0, 0)
    return w


def _run(world, **spec):
    return world.run(world.spec(ram_target=RamTarget(world.ledger, 'claim-a'), **spec))


def _memory(world, by_step):
    """``by_step[n]``: ``{pid: (ws_bytes, commit_bytes)}`` from the tick after wait ``n``; then the root exits."""
    for step, memory in by_step.items():
        world.steps[step] = (lambda m=memory: world.api.memory.update(m))
    world.steps[max(by_step) + 1] = lambda: setattr(world.root, 'alive', False)


class TestPublishing:

    def test_every_tick_publishes_the_members_sums_in_mb(self, world):
        world.api.memory.update({ROOT_PID: (2 * GB, 3 * GB), SCW: (1 * GB, 1 * GB)})
        world.steps[3] = lambda: setattr(world.root, 'alive', False)
        _run(world)
        assert world.ledger.published and set(world.ledger.published) == {(3072, 4096)}

    def test_the_phase_keeps_its_peak_working_set(self, world):
        _memory(world, {1: {ROOT_PID: (2 * GB, 4 * GB)}, 2: {ROOT_PID: (9 * GB, 12 * GB)},
                        3: {ROOT_PID: (5 * GB, 13 * GB)}})
        outcome = _run(world)
        assert outcome.ram.ws_mb == 9 * 1024 + 100   # the root's peak plus the SCW's 100 MiB at that tick
        assert (5 * 1024 + 100, 13 * 1024 + 150) in world.ledger.published   # current, not the peak

    def test_the_launch_is_framed_in_the_ledger(self, world):
        world.steps[2] = lambda: setattr(world.root, 'alive', False)
        seen = {}
        world.steps[1] = lambda: seen.update(entry=world.ledger.snapshot()['claim-a'])
        _run(world)
        assert seen['entry'].in_launch and seen['entry'].current_ws > 0
        after = world.ledger.snapshot()['claim-a']
        assert not after.in_launch and (after.current_ws, after.current_commit) == (0, 0) and not after.failed

    def test_a_member_that_exited_before_its_read_counts_nothing(self, world):
        reader = world.api.read_memory

        def exits_first(pid):   # listed by the Job Object, gone (and no longer a member) at the read
            if pid == SCW:
                world.procs[SCW].alive = False
            return reader(pid)
        world.api.read_memory = exits_first
        world.steps[3] = lambda: setattr(world.root, 'alive', False)
        outcome = _run(world)
        assert outcome.ram.ws_mb == 100 and world.ledger.failed_marks == 0

    def test_without_a_ram_target_the_figures_are_still_measured(self, world):
        world.steps[2] = lambda: setattr(world.root, 'alive', False)
        outcome = world.run()
        assert outcome.ram == PhaseRam(ws_mb=200, commit_mb=3072, process_commit_mb=2048)
        assert world.ledger.published == []

    def test_without_a_job_object_nothing_is_measured(self, world):
        world.steps[2] = lambda: setattr(world.root, 'alive', False)
        outcome = world.run(world.spec(ram_target=RamTarget(world.ledger, 'claim-a')), windows=False)
        assert outcome.ram == PhaseRam() and world.ledger.published == []


class TestFailedReads:

    def test_a_failed_read_of_a_member_makes_the_working_set_unknown(self, world, caplog):
        world.steps[2] = lambda: world.api.memory_fail.add(SCW)            # still a member
        world.steps[3] = lambda: world.api.memory_fail.discard(SCW)
        world.steps[5] = lambda: setattr(world.root, 'alive', False)
        outcome = _run(world)
        assert outcome.ram.ws_mb is None and outcome.ram.commit_mb == 3072   # the peak read still worked
        assert world.ledger.failed_marks == 1
        assert 'could not be read' in caplog.text

    def test_a_good_publish_clears_the_entry_but_not_the_figure(self, world):
        world.steps[2] = lambda: world.api.memory_fail.add(SCW)
        world.steps[3] = lambda: world.api.memory_fail.discard(SCW)
        seen = {}
        world.steps[4] = lambda: seen.update(entry=world.ledger.snapshot()['claim-a'])
        world.steps[5] = lambda: setattr(world.root, 'alive', False)
        outcome = _run(world)
        assert not seen['entry'].failed and outcome.ram.ws_mb is None

    def test_a_member_list_that_cannot_be_read_fails_closed(self, world):
        world.api.memory_fail.add(SCW)
        world.api.fail.add('pids')   # cannot tell whether the SCW is still a member
        ram = RunnerRam(RamTarget(world.ledger, 'claim-a'), reader=world.api.read_memory)
        ram.tick(JobObject(world.api, 77), [ROOT_PID, SCW])
        assert ram.result().ws_mb is None and world.ledger.failed_marks == 1


class TestPeakAfterExit:

    def test_the_peaks_are_read_after_the_exit_and_before_the_close(self, world):
        world.api.peak = (29 * GB + 5, 12 * GB)
        world.steps[2] = lambda: setattr(world.root, 'alive', False)
        outcome = _run(world)
        calls = world.api.calls
        assert calls.index('active') < calls.index('peak') < calls.index('close') == len(calls) - 1
        assert (outcome.ram.commit_mb, outcome.ram.process_commit_mb) == (29 * 1024, 12 * 1024)

    def test_a_failed_peak_read_is_unknown_and_marks_the_entry_failed(self, world, caplog):
        world.api.fail.add('peak')
        world.steps[2] = lambda: setattr(world.root, 'alive', False)
        outcome = _run(world)
        assert outcome.ram.commit_mb is None and outcome.ram.process_commit_mb is None
        assert outcome.ram.ws_mb == 200
        assert world.api.calls[-1] == 'close'   # the handle still closes
        entry = world.ledger.snapshot()['claim-a']
        assert entry.failed and not entry.in_launch   # held until the next launch or the release
        assert 'memory peak could not be read' in caplog.text

    def test_the_next_launch_clears_a_failed_peak(self, world):
        world.ledger.mark_failed('claim-a')
        assert world.ledger.launch_started('claim-a')
        assert not world.ledger.snapshot()['claim-a'].failed

    def test_a_zero_peak_is_not_a_figure(self, world):
        world.api.peak = (0, 0)
        world.steps[2] = lambda: setattr(world.root, 'alive', False)
        assert _run(world).ram.commit_mb is None


class TestEveryPathEndsTheLaunch:

    def test_a_refused_launch_ends_it(self, world):
        world.api.fail.add('assign')
        with pytest.raises(UnconfinedLaunchError):
            _run(world)
        assert not world.ledger.snapshot()['claim-a'].in_launch

    def test_an_exception_mid_monitor_ends_it(self, world):
        def boom(timeout_s):
            raise RuntimeError('a bug in the sampler')
        with pytest.raises(RuntimeError):
            _run(world, vram_sample=boom)
        assert not world.ledger.snapshot()['claim-a'].in_launch and 'close' in world.api.calls

    def test_a_tick_after_the_release_is_ignored(self, world):
        world.steps[2] = lambda: world.ledger.unregister('claim-a')
        world.steps[4] = lambda: setattr(world.root, 'alive', False)
        _run(world)
        assert world.ledger.snapshot() == {}


class TestJobObjectList:

    def test_a_member_psutil_cannot_open_is_still_read(self, world):
        """The RAM reads use the Job Object's own list, not the psutil processes the monitor could open."""
        hidden = FakePs(1002)
        world.procs[1002] = hidden
        world.api.memory[1002] = (4 * GB, 5 * GB)
        factory = {pid: proc for pid, proc in world.procs.items() if pid != 1002}

        def opener(pid):
            if pid == 1002:
                raise PermissionError('psutil cannot open it')
            return factory[pid]
        world.steps[2] = lambda: setattr(world.root, 'alive', False)
        outcome = process_runner.run(world.spec(ram_target=RamTarget(world.ledger, 'claim-a')),
                                     process_runner.RunnerRegistry(), job_api=world.api, popen=world.popen,
                                     clock=world.clock, windows=True, process_factory=opener, process_iter=lambda: [],
                                     ram_reader=world.api.read_memory)
        assert outcome.ram.ws_mb == 4 * 1024 + 200

    def test_an_unreadable_listed_member_is_a_failed_read(self, world):
        world.procs[1002] = FakePs(1002)
        world.api.memory_fail.add(1002)
        world.steps[2] = lambda: setattr(world.root, 'alive', False)
        assert _run(world).ram.ws_mb is None and world.ledger.failed_marks >= 1


class TestShaderWorkers:

    def test_a_shader_compile_worker_member_is_seen(self, world):
        world.steps[2] = lambda: setattr(world.root, 'alive', False)
        assert _run(world).shader_workers is True   # the fixture's SCW (ShaderCompileWorker.exe)

    def test_no_shader_compile_worker_means_none(self, tmp_path):
        world = World(tmp_path)
        world.steps[2] = lambda: setattr(world.root, 'alive', False)
        assert world.run().shader_workers is False

    def test_a_shader_worker_that_came_and_went_is_remembered(self, tmp_path):
        world = World(tmp_path)
        scw = FakePs(1009, exe='D:/UE_5.8/Engine/Binaries/Win64/ShaderCompileWorker.exe')
        scw.alive = False
        world.procs[1009] = scw
        world.steps[1] = lambda: setattr(scw, 'alive', True)
        world.steps[2] = lambda: setattr(scw, 'alive', False)
        world.steps[4] = lambda: setattr(world.root, 'alive', False)
        assert world.run().shader_workers is True
