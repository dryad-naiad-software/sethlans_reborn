# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the fits-nowhere triggers (Unreal per-machine memory
test D12, D16 (b), FR-MT-P-3, FR-MT-V-3, FR-MT-S-3 step (5)).

Every sweep runs from a real heartbeat POST whose on-commit callbacks are
captured and run, as Django runs them after the request's commit.

Covers: a ``self`` spill on a RENDERING job stops nothing while it renders
(its own on-commit check runs and finds the job RENDERING); the next sweep
after the job ended stops the key's QUEUED jobs.  A machine not tried
blocks until it goes off, then the next sweep stops the key; a failed
record of an online machine that is no longer an eligible pair does not
hide the key from the read.  Stop candidates never crowd a ``test`` row
out of the read's slice (roles ordered cancel, pilot, test, stop), and at
most ``SWEEP_MAX_KEYS`` stop rows are checked per sweep.  A final
``does_not_fit`` pilot result stops the key at once from its on-commit
check when nothing renders, and not while another machine is testing.  A
sweep with nothing due stays exactly one read with no lock when a
``does_not_fit`` record exists but the read rules the key out (a fits
record elsewhere, a job of the key rendering, another machine testing).
A stop candidate the read cannot settle (an online machine not tried, a
result due a re-test) is confirmed by ``stop_due`` and takes no lock.  A
failing check of one key is logged and the other keys still stop.
"""

import pytest

from workers.models import Job, Worker
from workers.services import fits_nowhere, machine_sweep

from .fits_nowhere_helpers import (
    GPU, GPU_TOTAL, LOWER, dnf_record, error_of, go_offline, statuses, stop_text, sweep_sql,
)
from .machine_test_helpers import heartbeat, pair_record, render_job
from .machine_verdict_helpers import TOTAL, dnf_body
from .memory_report_helpers import accepted, rendering_render, self_spill
from .unreal_helpers import caps, unreal_asset
from .unreal_pilot_helpers import KIND, claim, claimed_pilot, created_job, post_result, ready_worker, settings


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.fixture
def worker(worker_with_token):
    return ready_worker(worker_with_token[0]), worker_with_token[1]


@pytest.mark.django_db
class TestSweepStops:

    def test_not_while_the_job_renders_then_on_the_next_sweep(self, asset, worker,
                                                              django_capture_on_commit_callbacks):
        tested, client = worker
        rendering, record = rendering_render(asset, tested, client)
        queued = render_job(asset, name='MtFnQueued')
        with django_capture_on_commit_callbacks(execute=True):
            accepted(client, rendering, self_spill(record), result='does_not_fit')
        assert statuses(rendering, queued) == ['RENDERING', 'QUEUED']
        heartbeat(client, tested, django_capture_on_commit_callbacks)
        assert statuses(queued) == ['QUEUED']
        Job.objects.filter(pk=rendering.pk).update(status='DONE')
        heartbeat(client, tested, django_capture_on_commit_callbacks)
        assert statuses(rendering, queued) == ['DONE', 'ERROR']
        # The spill's lower bound is the result peak (8102 > the report's job peak 4000).
        assert error_of(queued) == stop_text('Example GPU', TOTAL, tested.hostname, 8102)

    def test_a_machine_not_tried_blocks_until_it_goes_off(self, asset, worker, make_worker_client,
                                                          django_capture_on_commit_callbacks):
        tested, client = worker
        job = render_job(asset)
        untried = ready_worker(make_worker_client('mt-fn-sweep-untried')[0])
        dnf_record(asset, job.unreal_target_sha256, tested)
        heartbeat(client, tested, django_capture_on_commit_callbacks)
        assert statuses(job) == ['QUEUED']
        go_offline(untried)
        heartbeat(client, tested, django_capture_on_commit_callbacks)
        assert statuses(job) == ['ERROR']
        assert error_of(job) == stop_text(GPU, GPU_TOTAL, tested.hostname, LOWER)

    def test_a_failed_record_of_a_machine_no_longer_eligible_does_not_hide_the_key(
            self, asset, worker, make_worker_client, django_capture_on_commit_callbacks):
        tested, client = worker
        job = render_job(asset)
        dropped = ready_worker(make_worker_client('mt-fn-sweep-dropped')[0])
        pair_record(asset, job.unreal_target_sha256, dropped, pilot='ERROR', error='boom')
        Worker.objects.filter(pk=dropped.pk).update(unreal_capabilities=caps(builds=('5.7.0-1',)))
        dnf_record(asset, job.unreal_target_sha256, tested)
        heartbeat(client, tested, django_capture_on_commit_callbacks)
        assert statuses(job) == ['ERROR']

    def test_stop_candidates_do_not_crowd_out_a_test_row(self, asset, worker, mocker,
                                                         django_capture_on_commit_callbacks):
        tested, client = worker
        mocker.patch.object(machine_sweep, 'SWEEP_MAX_KEYS', 2)
        mocker.patch.object(machine_sweep, 'MAX_OPEN_PILOTS', 1)  # the read's slice: 2 * 2 + 1 = 5 rows
        stops = [render_job(asset, name=f'MtFnCrowd{n}', render_settings=settings(preset=f'/Game/P/C{n}.C{n}'))
                 for n in range(6)]
        for job in stops:
            dnf_record(asset, job.unreal_target_sha256, tested)
        untested = render_job(asset, name='MtFnCrowdTest', render_settings=settings(preset='/Game/P/T.T'))
        heartbeat(client, tested, django_capture_on_commit_callbacks)
        [pilot] = Job.objects.filter(unreal_kind='pilot', target_worker=tested)
        assert pilot.unreal_target_sha256 == untested.unreal_target_sha256
        assert statuses(*stops).count('ERROR') == 2  # at most SWEEP_MAX_KEYS stop rows per sweep


@pytest.mark.django_db
class TestPilotResultTrigger:

    def test_a_final_does_not_fit_pilot_stops_the_key_at_once(self, admin_client, asset, worker,
                                                              django_capture_on_commit_callbacks):
        tested, client = worker
        pilot = claimed_pilot(admin_client, asset, tested, client)
        normal = Job.objects.get(name='Still01')
        with django_capture_on_commit_callbacks(execute=True):
            assert post_result(client, pilot.pk, dnf_body()).status_code == 200
        assert statuses(pilot, normal) == ['DONE', 'ERROR']
        assert error_of(normal) == stop_text('NVIDIA GeForce RTX 5090', TOTAL, tested.hostname, 8102)

    def test_not_while_another_machine_is_testing(self, admin_client, asset, worker, make_worker_client,
                                                  django_capture_on_commit_callbacks):
        tested, client = worker
        ready_worker(make_worker_client('mt-fn-pilot-other')[0])
        created_job(admin_client, asset)
        pilot = Job.objects.get(unreal_kind='pilot', target_worker=tested)
        assert claim(client, pilot, tested).status_code == 200
        assert Job.objects.filter(unreal_kind='pilot', status='QUEUED').exclude(target_worker=tested).count() == 1
        with django_capture_on_commit_callbacks(execute=True):
            assert post_result(client, pilot.pk, dnf_body()).status_code == 200
        assert statuses(Job.objects.get(name='Still01')) == ['QUEUED']


@pytest.mark.django_db
class TestCheapSweeps:

    @pytest.fixture
    def spies(self, mocker):
        return {
            'locks': [mocker.patch.object(module, name, wraps=getattr(module, name))
                      for module in (machine_sweep, fits_nowhere) for name in ('lock_project', 'lock_workers')],
            'stop_due': mocker.patch.object(machine_sweep, 'stop_due', wraps=machine_sweep.stop_due),
            'check': mocker.patch.object(machine_sweep, 'check', wraps=machine_sweep.check),
        }

    @staticmethod
    def _no_lock(sql, spies):
        assert not [query for query in sql if 'FOR UPDATE' in query], sql
        assert [spy.call_count for spy in spies['locks']] == [0, 0, 0, 0]
        spies['check'].assert_not_called()

    def _fits_elsewhere(self, job, other):
        pair_record(job.asset, job.unreal_target_sha256, other, verdict='fits')
        go_offline(other)

    def _rendering(self, job, other):
        render_job(job.asset, name='MtFnCheapRendering', status='RENDERING', assigned_worker=other)
        go_offline(other)

    def _testing(self, job, other):
        pair_record(job.asset, job.unreal_target_sha256, other, pilot='QUEUED')

    @pytest.mark.parametrize('rule_out', ['_fits_elsewhere', '_rendering', '_testing'])
    def test_a_ruled_out_key_is_still_one_read(self, asset, worker, make_worker_client, spies, rule_out,
                                               django_capture_on_commit_callbacks):
        tested, client = worker
        job = render_job(asset)
        dnf_record(asset, job.unreal_target_sha256, tested)
        getattr(self, rule_out)(job, ready_worker(make_worker_client('mt-fn-cheap-other')[0]))
        sql = sweep_sql(client, tested, django_capture_on_commit_callbacks)
        assert len(sql) == 1 and '"workers_job"' in sql[0], sql
        self._no_lock(sql, spies)
        spies['stop_due'].assert_not_called()
        assert statuses(job) == ['QUEUED']

    @pytest.mark.parametrize('other_state', [None, dict(verdict='does_not_fit', stale='driver_changed')],
                             ids=['not-tried', 'retest-due'])
    def test_a_candidate_the_read_cannot_settle_takes_no_lock(self, asset, worker, make_worker_client, spies,
                                                              other_state, django_capture_on_commit_callbacks):
        tested, client = worker
        job = render_job(asset)
        dnf_record(asset, job.unreal_target_sha256, tested)
        other = ready_worker(make_worker_client('mt-fn-unsettled')[0])
        if other_state is not None:
            pair_record(asset, job.unreal_target_sha256, other, **other_state)
        sql = sweep_sql(client, tested, django_capture_on_commit_callbacks)
        self._no_lock(sql, spies)
        spies['stop_due'].assert_called_once_with(asset.pk, asset.project.unreal_overrides_sha256,
                                                  job.unreal_target_sha256)
        assert statuses(job) == ['QUEUED']


@pytest.mark.django_db
class TestFailureIsolation:

    def test_a_failing_check_is_logged_and_the_other_keys_stop(self, asset, worker, mocker,
                                                               django_capture_on_commit_callbacks):
        tested, client = worker
        broken = render_job(asset, name='MtFnBroken')
        fine = render_job(asset, name='MtFnFine', render_settings=settings(preset='/Game/P/Fine.Fine'))
        for job in (broken, fine):
            dnf_record(asset, job.unreal_target_sha256, tested, KIND)
        real = fits_nowhere.check

        def check(project_id, asset_id, overrides, target_hash):
            if target_hash == broken.unreal_target_sha256:
                raise RuntimeError('simulated failure')
            return real(project_id, asset_id, overrides, target_hash)

        mocker.patch.object(machine_sweep, 'check', side_effect=check)
        logger = mocker.patch.object(machine_sweep, 'logger')
        heartbeat(client, tested, django_capture_on_commit_callbacks)
        assert statuses(broken, fine) == ['QUEUED', 'ERROR']
        [call] = [call for call in logger.method_calls if call[0] in ('warning', 'error', 'exception')]
        assert asset.project_id in call.args and call.kwargs.get('exc_info') is True
