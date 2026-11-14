# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``job_selection.select_claimable_job`` (Unreal foundation FR-WX-6,
FR-WX-13, D1; FR-T-5 "job_selection" and "idle-only eligibility").
"""
import pytest

from sethlans_worker_agent import job_processor, job_selection
from sethlans_worker_agent.executors import RendererReservation

from ._fakes import blender_job, install_capacity, unreal_job


@pytest.fixture()
def clock(monkeypatch):
    now = {'t': 1000.0}
    monkeypatch.setattr(job_selection, '_clock', lambda: now['t'])
    return now


class TestFirstEligible:

    def test_blender_only_claims_first_job(self):
        jobs = [blender_job(1), blender_job(2), blender_job(3)]
        selection = job_selection.select_claimable_job(jobs, install_capacity())
        assert selection.job is jobs[0]
        assert selection.capacity_mode == 'shared'
        assert isinstance(selection.renderer_reservation, RendererReservation)

    @pytest.mark.parametrize('extra', [{}, {'renderer': None}, {'renderer': ''}])
    def test_missing_or_null_renderer_is_blender(self, extra):
        job = blender_job(1, **extra)
        selection = job_selection.select_claimable_job([job], install_capacity())
        assert selection.job is job
        assert selection.capacity_mode == 'shared'

    def test_unknown_renderer_skipped_without_raising(self, caplog):
        caplog.set_level('INFO', logger='sethlans_worker_agent.job_selection')
        jobs = [blender_job(1, renderer='MAYA'), blender_job(2)]
        selection = job_selection.select_claimable_job(jobs, install_capacity())
        assert selection.job is jobs[1]
        assert "Unsupported renderer 'MAYA'" in caplog.text

    def test_unreal_without_executor_is_skipped(self):
        """Chunk 3: no unreal_executor.executor, so Unreal jobs are skipped."""
        jobs = [unreal_job(1), blender_job(2)]
        selection = job_selection.select_claimable_job(jobs, install_capacity())
        assert selection.job is jobs[1]

    def test_ineligible_job_skipped(self, fake_unreal):
        fake_unreal.eligible = False
        jobs = [unreal_job(1), blender_job(2)]
        selection = job_selection.select_claimable_job(jobs, install_capacity())
        assert selection.job is jobs[1]

    def test_try_reserve_raising_is_skipped(self, fake_unreal, mocker):
        mocker.patch.object(fake_unreal, 'try_reserve', side_effect=RuntimeError('x'))
        jobs = [unreal_job(1), blender_job(2)]
        selection = job_selection.select_claimable_job(jobs, install_capacity())
        assert selection.job is jobs[1]

    def test_unfinished_job_id_skipped(self, fake_unreal, caplog):
        caplog.set_level('INFO', logger='sethlans_worker_agent.job_selection')
        with job_processor._active_jobs_lock:
            job_processor._unfinished_job_ids.update({1, 2})
        jobs = [blender_job(1), unreal_job(2), blender_job(3)]
        selection = job_selection.select_claimable_job(jobs, install_capacity())
        assert selection.job is jobs[2]
        assert fake_unreal.token_free()  # skipped before try_reserve
        assert 'previous run still finishing on this worker' in caplog.text

    def test_nothing_eligible_returns_none(self, fake_unreal):
        fake_unreal.eligible = False
        assert job_selection.select_claimable_job(
            [unreal_job(1)], install_capacity()) is None
        assert job_selection.select_claimable_job([], install_capacity()) is None


class TestIdleOnlyUnreal:

    def test_fully_idle_older_unreal_wins(self, fake_unreal):
        jobs = [unreal_job(1), blender_job(2)]
        selection = job_selection.select_claimable_job(jobs, install_capacity())
        assert selection.job is jobs[0]
        assert selection.capacity_mode == 'exclusive'

    def test_fully_idle_older_blender_wins(self, fake_unreal):
        jobs = [blender_job(1), unreal_job(2)]
        selection = job_selection.select_claimable_job(jobs, install_capacity())
        assert selection.job is jobs[0]
        assert fake_unreal.token_free()

    def test_partially_busy_machine_takes_blender(self, fake_unreal):
        cap = install_capacity(gpu_slot_count=2, cpu_slot_count=0)
        assert cap.reserve_for_job(900, 'GPU') is not None
        jobs = [unreal_job(1), blender_job(2)]
        selection = job_selection.select_claimable_job(jobs, cap)
        assert selection.job is jobs[1]
        assert fake_unreal.token_free()
        # Nothing is kept that delays later Blender claims.
        again = job_selection.select_claimable_job([blender_job(3)], cap)
        assert again.job['id'] == 3


class TestPendingProbe:

    def test_first_job_pending_waits_once_then_walks_on(self, fake_unreal):
        fake_unreal.pending_ids.add(1)
        jobs = [unreal_job(1), blender_job(2)]
        cap = install_capacity()
        assert job_selection.select_claimable_job(jobs, cap) is None
        selection = job_selection.select_claimable_job(jobs, cap)
        assert selection.job is jobs[1]

    def test_later_job_pending_never_waits(self, fake_unreal):
        fake_unreal.pending_ids.add(2)
        jobs = [blender_job(1, renderer='MAYA'), unreal_job(2), blender_job(3)]
        selection = job_selection.select_claimable_job(jobs, install_capacity())
        assert selection.job is jobs[2]

    def test_negative_probe_result_never_waits(self, fake_unreal):
        fake_unreal.eligible = False  # probe answered "missing"
        jobs = [unreal_job(1), blender_job(2)]
        selection = job_selection.select_claimable_job(jobs, install_capacity())
        assert selection.job is jobs[1]


class TestSkipRecords:

    def test_skip_logged_once_per_job_per_ttl(self, clock, caplog):
        caplog.set_level('INFO', logger='sethlans_worker_agent.job_selection')
        jobs = [blender_job(1, renderer='MAYA'), blender_job(2)]
        cap = install_capacity()
        job_selection.select_claimable_job(jobs, cap)
        job_selection.select_claimable_job(jobs, cap)
        assert caplog.text.count('Skipping MAYA job 1') == 1
        clock['t'] += job_selection.SKIP_RECORD_TTL_SECONDS + 1
        job_selection.select_claimable_job(jobs, cap)
        assert caplog.text.count('Skipping MAYA job 1') == 2

    def test_records_pruned_after_ttl(self, clock):
        cap = install_capacity()
        job_selection.select_claimable_job([blender_job(1, renderer='X'), blender_job(2)], cap)
        assert 1 in job_selection._skip_records
        clock['t'] += job_selection.SKIP_RECORD_TTL_SECONDS + 1
        job_selection.select_claimable_job([blender_job(3)], cap)
        assert job_selection._skip_records == {}

    def test_records_capped(self, clock, monkeypatch):
        monkeypatch.setattr(job_selection, 'SKIP_RECORD_MAX_ENTRIES', 5)
        cap = install_capacity()
        for job_id in range(1, 9):
            clock['t'] += 1
            job_selection.select_claimable_job([blender_job(job_id, renderer='X')], cap)
        assert len(job_selection._skip_records) == 5
        assert sorted(job_selection._skip_records) == [4, 5, 6, 7, 8]
