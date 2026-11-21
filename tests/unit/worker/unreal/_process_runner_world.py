# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The ``World`` of the UE process runner tests: a fake Win32 job, Popen, psutil and a manual clock."""
from sethlans_worker_agent.unreal_executor import process_runner

from ._runner_fakes import ROOT_PID, Clock, FakeJobApi, FakePopen, FakePs, make_spec


class World:
    def __init__(self, tmp_path, kids=()):
        self.tmp_path = tmp_path
        self.clock = Clock()
        self.root = FakePs(ROOT_PID, cpu=0.0)
        self.procs = {ROOT_PID: self.root}
        for kid in kids:
            self.procs[kid.pid] = kid
        self.api = FakeJobApi(self.procs)
        self.popen = FakePopen(self.procs)
        self.steps = {}

    def scenario(self, tick):
        step = self.steps.get(tick)
        if step is not None:
            step()

    def log(self, *lines):
        with open(self.tmp_path / 'render.1.log', 'a', encoding='utf-8') as handle:
            handle.write(''.join(line + '\n' for line in lines))

    def spec(self, **overrides):
        return make_spec(self.tmp_path, self.clock, self.scenario, **overrides)

    def run(self, spec=None, registry=None, windows=True, exit_wait_s=30.0):
        return process_runner.run(spec or self.spec(), registry or process_runner.RunnerRegistry(),
                                  job_api=self.api, popen=self.popen,
                                  clock=self.clock, windows=windows, process_factory=lambda pid: self.procs[pid],
                                  process_iter=lambda: [], exit_wait_s=exit_wait_s, ram_reader=self.api.read_memory)
