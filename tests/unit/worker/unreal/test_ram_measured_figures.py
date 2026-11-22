# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The RAM gate with the measured figures (two 20 GB AMD cards; render per card "Measured", D9,
FR-PC-RAM-4): commit limit 71,045 MB; a cold render about 30.7 GB commit and 12.3 GB working set, a warm
one 17.6 / 7.6. A cold pair is declined by the commit check; a warm pair is admitted while the base commit
is under about 27 GB. A claim's need drops to its render figure once its warm-up's process tree has
exited (never while it may still run), so a warm second job is admitted after the first job's cold warm-up.
"""
import pytest

from sethlans_worker_agent.capacity import WorkerCapacity
from sethlans_worker_agent.unreal_executor import cvars, ram_ledger, reserve_local, warmup
from sethlans_worker_agent.unreal_executor.job_ram import RamNeed
from sethlans_worker_agent.unreal_executor.runner_ram import RamTarget, RunnerRam
from tests.unit.worker.executors._fakes import profile
from tests.unit.worker.unreal._executor_fakes import job, machine_result, params
from tests.unit.worker.unreal.test_ram_gate import SETTINGS, readers, reserver
from tests.unit.worker.unreal.test_runner_ram import world  # noqa: F401 - the fixture

COMMIT_LIMIT = 71045
COLD = RamNeed(ws_mb=12320, commit_mb=30699, source='measured', warmup=True)
WARM = RamNeed(ws_mb=7568, commit_mb=17630, source='measured', warmup=False)
FREE_RAM = 64 * 1024 - 12000   # physical never binds in these cases
MEASURED = {'prep': {'ws_mb': 3913, 'commit_mb': 5280},
            'warmup': {'ws_mb': 12320, 'commit_mb': 30699, 'cold': True},
            'render': {'ws_mb': 7568, 'commit_mb': 17630}}


def pair(need, base_commit):
    """The second job's decision while the first (same need) is registered and not yet launched."""
    ledger = ram_ledger.RamLedger()
    ledger.register('first', need.ws_mb, need.commit_mb)
    return reserve_local.check(need, ledger, readers(FREE_RAM, COMMIT_LIMIT - base_commit), SETTINGS)


class TestMeasuredPairs:

    @pytest.mark.parametrize('base_commit', [1456, 8000, 20000])
    def test_a_cold_pair_is_declined_by_the_commit_check(self, base_commit):
        """30,699 x 2 + the 8,192 reserve = 69,590 MB: only a base commit up to 1,455 MB would fit."""
        decision = pair(COLD, base_commit)
        assert not decision.ok and decision.message.startswith('needs 30699 MB of commit;')
        assert pair(COLD, 1455).ok   # the edge, never reached on a real machine

    @pytest.mark.parametrize('base_commit', [8000, 20000, 27593])
    def test_a_warm_pair_is_admitted_under_about_27_gb_of_base_commit(self, base_commit):
        assert pair(WARM, base_commit).ok

    def test_a_warm_pair_is_declined_above_it(self):
        decision = pair(WARM, 27594)
        assert not decision.ok and decision.message.startswith('needs 17630 MB of commit;')


class TestNeedAfterTheWarmUp:

    def test_the_drop_needs_the_warm_up_tree_gone(self):
        ledger = ram_ledger.RamLedger()
        ledger.register('c', 12320, 30699)
        target = RamTarget(ledger, 'c', (12320, 30699), (7568, 17630))
        ram = RunnerRam(target, phase='warmup')
        ram.launch_started()
        ram.launch_ended(tree_exited=False)               # survivors: the warm-up may still hold memory
        assert ledger.snapshot()['c'].need_commit == 30699
        ram.launch_ended(tree_exited=True)
        assert (ledger.snapshot()['c'].need_ws, ledger.snapshot()['c'].need_commit) == (7568, 17630)
        RunnerRam(target, phase='warmup').launch_started()   # the FR-UE-10 retry warm-up: full need again
        assert ledger.snapshot()['c'].need_commit == 30699

    @pytest.mark.parametrize('phase', ['prep', 'render'])
    def test_other_phases_leave_the_need(self, phase):
        ledger = ram_ledger.RamLedger()
        ledger.register('c', 12320, 30699)
        ram = RunnerRam(RamTarget(ledger, 'c', (12320, 30699), (7568, 17630)), phase=phase)
        ram.launch_started()
        ram.launch_ended(tree_exited=True)
        assert ledger.snapshot()['c'].need_commit == 30699

    def test_a_warm_up_launch_lowers_it_once_its_tree_exited(self, world):  # noqa: F811
        ledger = ram_ledger.RamLedger()
        ledger.register('c', 12320, 30699)
        world.steps[2] = lambda: setattr(world.root, 'alive', False)
        seen = {}
        world.steps[1] = lambda: seen.update(during=ledger.snapshot()['c'].need_commit)
        world.run(world.spec(phase='warmup', ram_target=RamTarget(ledger, 'c', (12320, 30699), (7568, 17630))))
        assert seen['during'] == 30699 and ledger.snapshot()['c'].need_commit == 17630

    def test_a_warm_second_job_is_admitted_after_the_first_jobs_cold_warm_up(self, tmp_path):
        p = params()
        slot1 = warmup.marker_path(tmp_path, p.overlay_sha256, p.engine_build, 1)   # slot 1 warm, slot 0 cold
        warmup.record_success(slot1, p.map, p.sequence, cvars.warmup_cvars_sha256(p.merged))
        cap = WorkerCapacity(profile(gpu_slot_count=0))
        headroom = COMMIT_LIMIT - 20000   # 51,045: fits 17,630 + 17,630 + 8,192, not 30,699 + 17,630 + 8,192
        r = reserver(cap, ram_readers=readers(FREE_RAM, headroom), entry_dir=lambda sha: tmp_path)
        first = r.try_reserve(job(job_id=1, unreal_machine_results=[machine_result(ram=MEASURED)]))
        assert (first.slot, first.ram_need.commit_mb, first.ram_need.warmup) == (0, 30699, True)
        cap.reserve_unreal_card(1, first.card_key)
        second = job(job_id=2, unreal_machine_results=[machine_result(ram=MEASURED)])
        assert r.try_reserve(second) is None   # its cold warm-up may run

        warm_up = RunnerRam(first.ram_target(), phase='warmup')
        warm_up.launch_started()
        warm_up.launch_ended(tree_exited=True)
        second = r.try_reserve(job(job_id=2, unreal_machine_results=[machine_result(ram=MEASURED)]))
        assert second is not None and second.slot == 1
        assert (second.ram_need.commit_mb, second.ram_need.warmup) == (17630, False)
