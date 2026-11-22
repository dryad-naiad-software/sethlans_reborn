# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the fits-nowhere message (Unreal per-machine memory
test FR-MT-N-2, D12).

Covers: the message names the largest card tried among the **online**
pairs' ``does_not_fit`` records (largest ``gpu_total_mb``; ties go to the
larger lower bound; an offline machine with a bigger card is ignored),
its total, the machine's host name, the lower bound and each job's own
output size text, with the three options, word for word; the card name
falls back to the card kind and the lower bound to the result peak
(``max(peak_mb, job_peak_max_mb)``); a job whose size was set on it reads
"the job's size"; one WARNING is logged per stop.
"""

import pytest

from workers.models import UnrealMeasurement
from workers.services import fits_nowhere

from .fits_nowhere_helpers import GPU, GPU_TOTAL, LOWER, check_key, dnf_record, error_of, go_offline, stop_text
from .machine_test_helpers import KIND_AMD, kinds_worker, render_job
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import KIND, ready_worker, settings


@pytest.fixture
def job(db):
    return render_job(unreal_asset())


@pytest.fixture
def three(worker_with_token, make_worker_client):
    """Three eligible machines with one card kind each."""
    return [ready_worker(worker_with_token[0])] + [
        ready_worker(make_worker_client(name)[0]) for name in ('mt-fn-msg-b', 'mt-fn-msg-c')]


def _dnf(job, worker, kind=KIND, **fields):
    return dnf_record(job.asset, job.unreal_target_sha256, worker, kind, **fields)


@pytest.mark.django_db
class TestLargestCard:

    def test_the_largest_total_wins_over_a_larger_lower_bound(self, job, three):
        a, b, c = three
        _dnf(job, a, total=16303, lower=15900, name='Card 16')
        _dnf(job, b, total=GPU_TOTAL, lower=LOWER, name=GPU)
        _dnf(job, c, total=20464, lower=19800, name='Card 20')
        assert check_key(job) == 1
        assert error_of(job) == stop_text(GPU, GPU_TOTAL, b.hostname, LOWER)

    def test_a_tie_goes_to_the_larger_lower_bound(self, job, three):
        a, b, c = three
        _dnf(job, a, lower=21000, name='Card A')
        _dnf(job, b, lower=23900, name='Card B')
        _dnf(job, c, lower=22000, name='Card C')
        assert check_key(job) == 1
        assert error_of(job) == stop_text('Card B', GPU_TOTAL, b.hostname, 23900)

    def test_an_offline_machine_with_a_bigger_card_is_ignored(self, job, three):
        a, b, c = three
        _dnf(job, a, total=16303, lower=15900, name='Card 16')
        _dnf(job, b)
        _dnf(job, c, total=49140, lower=48000, name='Card 48')
        go_offline(c)
        assert check_key(job) == 1
        assert error_of(job) == stop_text(GPU, GPU_TOTAL, b.hostname, LOWER)

    def test_the_largest_kind_of_a_machine_with_two(self, job, three):
        a, _b, _c = three
        for other in three[1:]:
            go_offline(other)
        kinds_worker(a, KIND, KIND_AMD)
        _dnf(job, a, total=16303, lower=15900, name='Card 16')
        _dnf(job, a, KIND_AMD, total=20464, lower=19800, name='Card 20')
        assert check_key(job) == 1
        assert error_of(job) == stop_text('Card 20', 20464, a.hostname, 19800)


@pytest.mark.django_db
class TestFallbacks:

    @pytest.fixture
    def alone(self, worker_with_token):
        return ready_worker(worker_with_token[0])

    def test_the_card_name_falls_back_to_the_card_kind(self, job, alone):
        _dnf(job, alone, name='')
        check_key(job)
        assert error_of(job) == stop_text(KIND, GPU_TOTAL, alone.hostname, LOWER)

    @pytest.mark.parametrize('peak,job_peak,expected', [
        (8102, 9100, 9100), (9300, 9100, 9300), (8102, None, 8102),
    ], ids=['job-peak-larger', 'pilot-peak-larger', 'no-job-peak'])
    def test_the_lower_bound_falls_back_to_the_result_peak(self, job, alone, peak, job_peak, expected):
        _dnf(job, alone, lower=None, peak=peak, job_peak_max_mb=job_peak)
        check_key(job)
        assert error_of(job) == stop_text(GPU, GPU_TOTAL, alone.hostname, expected)

    def test_the_host_is_the_machines_name(self, job, alone):
        record = _dnf(job, alone)
        UnrealMeasurement.objects.filter(pk=record.pk).update(worker_hostname='mt-fn-earlier-name')
        check_key(job)
        assert error_of(job) == stop_text(GPU, GPU_TOTAL, alone.hostname, LOWER)


@pytest.mark.django_db
class TestSizeAndLog:

    def test_a_size_set_on_the_job_reads_the_jobs_size(self, worker_with_token):
        alone = ready_worker(worker_with_token[0])
        job = render_job(unreal_asset(), render_settings=settings(resolution=[1920, 1080]))
        _dnf(job, alone)
        check_key(job)
        assert error_of(job) == stop_text(GPU, GPU_TOTAL, alone.hostname, LOWER, "at 1920x1080 (the job's size)")

    def test_one_warning_per_stop(self, job, worker_with_token, mocker):
        alone = ready_worker(worker_with_token[0])
        render_job(job.asset, name='MtFnMsgAgain')
        _dnf(job, alone)
        logger = mocker.patch.object(fits_nowhere, 'logger')
        assert check_key(job) == 2
        [call] = logger.method_calls
        assert call[0] == 'warning'
        assert call.args[1:] == (2, job.asset_id, job.unreal_target_sha256, GPU, GPU_TOTAL, alone.hostname)

    def test_no_warning_without_a_stop(self, job, worker_with_token, mocker):
        ready_worker(worker_with_token[0])
        logger = mocker.patch.object(fits_nowhere, 'logger')
        assert check_key(job) == 0
        assert logger.method_calls == []
