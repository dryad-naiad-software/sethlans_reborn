# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The runner's RAM figures against a real Job Object and real Windows memory counters (render per card
FR-PC-RAM-2): a harmless Python child commits and touches 256 MiB; the published working set and
commit and the launch's peaks must cover it. No Unreal.
"""
import sys

import pytest

from sethlans_worker_agent.unreal_executor import process_runner
from sethlans_worker_agent.unreal_executor.ram_ledger import RamLedger
from sethlans_worker_agent.unreal_executor.runner_ram import RamTarget

from .test_job_object_runner_real import child_writing, launch_for, profile, spec_for  # noqa: F401 - fixture

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows Job Objects and memory counters')
HELD_MB = 256


class Recording(RamLedger):
    def __init__(self):
        super().__init__()
        self.published = []

    def publish(self, claim_id, current_ws, current_commit):
        self.published.append((current_ws, current_commit))
        return super().publish(claim_id, current_ws, current_commit)


def test_a_child_holding_256_mib_is_measured_and_published(tmp_path, profile):  # noqa: F811 - the fixture
    log = tmp_path / 'phase.log'
    code = (f"block = b'\\x01' * ({HELD_MB} * 1024 * 1024); log.write('held\\n'); time.sleep(5); "
            "log.write('[Warm] done, quitting\\n')")
    ledger = Recording()
    ledger.register('claim-real', 0, 0)
    spec = spec_for(tmp_path, launch_for(profile, child_writing(log, code), 'warmup'),
                    ram_target=RamTarget(ledger, 'claim-real'))
    outcome = process_runner.run(spec, process_runner.RunnerRegistry())
    assert outcome.reason == 'exited' and outcome.survivors == ()
    ram = outcome.ram
    assert ram.ws_mb is not None and ram.ws_mb >= HELD_MB
    assert ram.commit_mb is not None and ram.commit_mb >= HELD_MB and ram.commit_mb >= ram.ws_mb
    assert ram.process_commit_mb is not None and HELD_MB <= ram.process_commit_mb <= ram.commit_mb
    assert max(ws for ws, _commit in ledger.published) >= HELD_MB
    assert max(commit for _ws, commit in ledger.published) >= HELD_MB
    entry = ledger.snapshot()['claim-real']
    assert not entry.in_launch and not entry.failed and (entry.current_ws, entry.current_commit) == (0, 0)
