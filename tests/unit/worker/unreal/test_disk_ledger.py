# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The disk check of pieces beside other Unreal jobs (render per card D12, FR-PC-DSK-1; AC-9, AC-11):
another running piece's unwritten frames count as used (its partial output as written), a running
pilot's folder is never free, a finished job's leftover frames are, a claim registered after the walk
counts as nothing written; every claim is registered at reservation and dropped on release and retain.
"""
import pytest

from sethlans_worker_agent import job_resources
from sethlans_worker_agent.unreal_executor import disk_ledger
from sethlans_worker_agent.unreal_executor.outcomes import LocalConflict
from tests.unit.worker.unreal._executor_fakes import job
from tests.unit.worker.unreal.test_reserve import make
from tests.unit.worker.unreal.test_slot_release import run  # noqa: F401 - the fixture

GIB = disk_ledger.GIB


def folder(jobs, name, written=0):
    out = jobs / name / 'output'
    out.mkdir(parents=True)
    if written:
        (out / 'frame.0001.exr').write_bytes(b'x' * written)
    return jobs / name


@pytest.fixture
def jobs(tmp_path):
    path = tmp_path / 'j'
    path.mkdir()
    return path


def ledger_with(free):
    return disk_ledger.DiskLedger(free_bytes=lambda path: free)


def running(ledger, claim_id, job_dir, need=0):
    ledger.register(claim_id)
    ledger.attach(claim_id, job_dir)
    if need:
        ledger.check_and_register(claim_id, job_dir, need, job_dir / 'output', 0)


class TestCheck:

    def test_another_pieces_partial_output_counts_as_written(self, jobs):
        ledger = ledger_with(10_000)
        a = folder(jobs, '1-aaaa', written=400)
        running(ledger, 'a', a, need=1000)           # 600 still to come
        own = folder(jobs, '2-bbbb')
        assert ledger.check_and_register('b', own, 9400, own / 'output', 0) == 9400
        ledger.unregister('b')
        with pytest.raises(LocalConflict) as caught:
            ledger.check_and_register('b', own, 9401, own / 'output', 0)
        assert caught.value.reason == 'local_conflict'

    def test_a_running_pilots_folder_is_never_free(self, jobs):
        ledger = ledger_with(1000)
        running(ledger, 'pilot', folder(jobs, '3-pilot', written=5000))   # need 0: nothing more to come
        own = folder(jobs, '4-own')
        with pytest.raises(LocalConflict):
            ledger.check_and_register('piece', own, 1001, own / 'output', 0)
        ledger.unregister('pilot')                                       # released: its frames go soon
        assert ledger.check_and_register('piece', own, 6000, own / 'output', 0) == 6000

    def test_a_finished_jobs_leftover_counts_as_free(self, jobs):
        ledger = ledger_with(1000)
        folder(jobs, '5-done', written=700)
        (jobs / '6-logs-only').mkdir()
        own = folder(jobs, '7-own', written=9)                          # its own folder is not a leftover
        assert ledger.check_and_register('piece', own, 1700, own / 'output', 0) == 1700
        ledger.unregister('piece')
        with pytest.raises(LocalConflict):
            ledger.check_and_register('piece', own, 1701, own / 'output', 0)

    def test_a_claim_registered_after_the_walk_counts_nothing_written(self, jobs):
        late_dir = folder(jobs, '8-late', written=300)
        own = folder(jobs, '9-own')
        walked = []

        def walk(job_dir):
            if not walked:   # the late claim arrives between the walk and the lock
                walked.append(job_dir)
                ledger.register('late')
                ledger.attach('late', late_dir)
                with ledger._lock:
                    ledger._claims['late'] = disk_ledger.Entry(need=500, job_dir=late_dir)
            return disk_ledger.output_bytes(job_dir)
        ledger = disk_ledger.DiskLedger(free_bytes=lambda path: 1000, walk=walk)
        # Its 300 bytes were seen as leftover by the walk, but it is running now: not free, and its
        # whole need of 500 counts (written 0): 1000 - 500 = 500 available.
        with pytest.raises(LocalConflict):
            ledger.check_and_register('own', own, 501, own / 'output', 0)
        assert ledger.check_and_register('own', own, 500, own / 'output', 0) == 500

    def test_the_reserve_and_the_message(self, jobs):
        own = folder(jobs, '1-own')
        ledger = ledger_with(3 * GIB)
        with pytest.raises(LocalConflict) as caught:
            ledger.check_and_register('c', own, 2 * GIB, own / 'output', 2, frames=60)
        assert caught.value.message == ("This piece needs up to 2.0 GB for its 60 frames, but the cache drive has "
                                        "only 1.0 GB free above the 2 GB reserve.")
        assert 'c' not in ledger.snapshot()   # refused: not registered

    def test_a_refusal_keeps_the_claims_estimate(self, jobs):
        own = folder(jobs, '1-own')
        ledger = ledger_with(100)
        ledger.register('c')
        with pytest.raises(LocalConflict):
            ledger.check_and_register('c', own, 101, own / 'output', 0)
        assert ledger.snapshot()['c'] == disk_ledger.Entry()

    def test_registry_rules(self, jobs):
        ledger = ledger_with(0)
        ledger.register('c')
        with pytest.raises(ValueError):
            ledger.register('c')
        assert ledger.attach('c', jobs) and not ledger.attach('gone', jobs)
        ledger.unregister('c')
        ledger.unregister('c')   # idempotent
        assert ledger.snapshot() == {}


class TestClaims:

    def test_every_claim_is_registered_with_need_0_and_dropped_at_release(self):
        reserver, _ = make()
        first, second = reserver.try_reserve(job(job_id=1)), reserver.try_reserve(job(job_id=2))
        assert reserver.disk.snapshot() == {first.claim_id: disk_ledger.Entry(), second.claim_id: disk_ledger.Entry()}
        assert first.disk_target() == disk_ledger.DiskTarget(reserver.disk, first.claim_id)
        first.release()
        assert set(reserver.disk.snapshot()) == {second.claim_id}
        second.retain_until_restart()   # AC-11: a retained claim keeps only its card and slot
        assert reserver.disk.snapshot() == {} and reserver.ram.snapshot() == {}

    def test_a_failed_disk_registration_holds_nothing(self, monkeypatch):
        reserver, _ = make()
        monkeypatch.setattr(reserver.disk, 'register', lambda claim_id: (_ for _ in ()).throw(ValueError('dup')))
        with pytest.raises(ValueError):
            reserver.try_reserve(job())
        assert reserver.ram.snapshot() == {} and reserver.slots.free() == tuple(range(16))

    def test_the_job_folder_is_attached_before_the_flow_and_handed_to_it(self, run):  # noqa: F811
        run.execute()
        entry = run.reserver.disk.snapshot()[run.reservation.claim_id]
        assert entry.job_dir == run.world.job.root and entry.need == 0
        assert run.world.flow.disk == disk_ledger.DiskTarget(run.reserver.disk, run.reservation.claim_id)
        assert job_resources.release_reservations(run.world.params.job_id, run.reservation, None) is True
        assert run.reservation.claim_id not in run.reserver.disk.snapshot()


class TestConcurrentCleanup:

    def test_frames_deleted_after_the_walk_are_not_counted_twice(self, jobs):
        """A finished job's 700 bytes: leftover in the walk, then deleted before the free-space read."""
        done = folder(jobs, '5-done', written=700)
        own = folder(jobs, '6-own')
        free = {'bytes': 1000}

        def walk(job_dir):
            size = disk_ledger.output_bytes(job_dir)
            if job_dir == done:   # its cleanup runs right after the walk saw it
                free['bytes'] += size
            return size
        ledger = disk_ledger.DiskLedger(free_bytes=lambda path: free['bytes'], walk=walk)
        with pytest.raises(LocalConflict):
            ledger.check_and_register('own', own, 1701, own / 'output', 0)   # 1700 + 700 would admit it
        assert ledger.check_and_register('own', own, 1700, own / 'output', 0) == 1700

    def test_a_released_claim_is_never_added_back(self, jobs):
        own = folder(jobs, '1-own')
        ledger = ledger_with(10 ** 9)
        ledger.register('c')
        ledger.unregister('c')
        assert ledger.check_and_register('c', own, 100, own / 'output', 0) == 100
        assert ledger.snapshot() == {}
