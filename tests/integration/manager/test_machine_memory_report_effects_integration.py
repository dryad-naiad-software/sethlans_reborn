# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: what an accepted ``memory-report/`` does to its result
(Unreal per-machine memory test FR-MT-S-3 steps (1), (3), (5); D6, D9,
D8, AC-7 first half).

Covers: the report and its answer are stored on the job; ``job_peak_mb``
raises ``job_peak_max_mb`` only when larger; a raised result peak at
D6's line stays ``fits``, one MB over turns ``does_not_fit``
(``job_peak``); a record without card figures is never marked; a ``self``
spill turns the record ``does_not_fit`` (``spill``) with ``lower_bound_mb
= max(result peak, job_peak_mb)``; ``contention``, ``spilled`` false and
null change no verdict; a derived record's change also marks its current
``fits`` covering record, never a covering record that is no longer
current or fits, nor one of another machine or card kind (left alone with
a WARNING); a ``does_not_fit`` record is not touched; a second job of
the key builds on the first one's raised peak; a standalone job adds no
warning anywhere.  The fits-nowhere check is registered ``on_commit``
(not run inline) only for a verdict changed: the record's key, and the
covering record's key when it changed; a raised peak or contention
alone registers nothing.
"""

import pytest

from workers.models import SequenceSubmission, UnrealMeasurement

from .machine_test_helpers import KIND_AMD, kinds_worker, render_job
from .machine_verdict_helpers import LARGEST_FITS_PEAK
from .memory_report_helpers import (
    accepted, contention, derive_from, keyed_record, rendering_render, report_body, self_spill, state,
    stored_report,
)
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import KIND, claim

CHECK = 'workers.services.fits_nowhere.check'


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.fixture
def caller(worker_with_token):
    worker, client = worker_with_token
    return kinds_worker(worker, KIND), client


@pytest.fixture
def rendering(asset, caller):
    worker, client = caller
    job, record = rendering_render(asset, worker, client)
    return job, record, worker, client


def _with(asset, caller, **fields):
    worker, client = caller
    job, record = rendering_render(asset, worker, client, **fields)
    return job, record, client


def _key(record):
    return (record.project_id, record.asset_id, record.overrides_sha256, record.target_sha256)


@pytest.mark.django_db
class TestJobPeak:

    def test_the_report_and_answer_are_stored(self, rendering):
        job, record, _worker, client = rendering
        body = report_body(record, job_peak_mb=9000)
        accepted(client, job, body)
        assert stored_report(job) == {'report': body, 'result': 'fits'}
        assert state(record)[:4] == ('fits', 'pilot', '', 9000)

    @pytest.mark.parametrize('job_peak', [8999, 9000, None], ids=['smaller', 'equal', 'null'])
    def test_a_smaller_or_null_peak_keeps_the_raised_one(self, asset, caller, job_peak):
        job, record, client = _with(asset, caller, job_peak_max_mb=9000)
        accepted(client, job, report_body(record, job_peak_mb=job_peak))
        assert state(record)[:5] == ('fits', 'pilot', '', 9000, None)

    def test_a_second_job_builds_on_the_first_ones_raised_peak(self, rendering):
        job, record, worker, client = rendering
        accepted(client, job, report_body(record, job_peak_mb=9000))
        second = render_job(job.asset, name='MtReportTwo')
        assert claim(client, second, worker).status_code == 200
        accepted(client, second, self_spill(record, job_peak_mb=None), result='does_not_fit')
        assert state(record)[:5] == ('does_not_fit', 'spill', '', 9000, 9000)

    def test_at_the_line_it_still_fits(self, rendering):
        job, record, _worker, client = rendering
        accepted(client, job, report_body(record, job_peak_mb=LARGEST_FITS_PEAK))
        assert state(record)[:5] == ('fits', 'pilot', '', LARGEST_FITS_PEAK, None)

    def test_one_mb_over_the_line_does_not_fit(self, rendering):
        job, record, _worker, client = rendering
        over = LARGEST_FITS_PEAK + 1
        accepted(client, job, report_body(record, job_peak_mb=over), result='does_not_fit')
        assert state(record)[:5] == ('does_not_fit', 'job_peak', '', over, over)

    def test_the_result_peak_includes_the_records_own_peak(self, asset, caller):
        # D2: result peak = max(peak_mb, job_peak_max_mb); a small job peak cannot hide a peak already over.
        over = LARGEST_FITS_PEAK + 5
        job, record, client = _with(asset, caller, peak=over)
        accepted(client, job, report_body(record, job_peak_mb=4000), result='does_not_fit')
        assert state(record)[:5] == ('does_not_fit', 'job_peak', '', 4000, over)

    @pytest.mark.parametrize('missing', [{'gpu_total_mb': None}, {'baseline_used_mb': None}],
                             ids=['no-total', 'no-baseline'])
    def test_without_card_figures_it_is_never_marked(self, asset, caller, missing):
        job, record, client = _with(asset, caller, **missing)
        accepted(client, job, report_body(record, job_peak_mb=60000))
        assert state(record)[:5] == ('fits', 'pilot', '', 60000, None)

    def test_a_does_not_fit_record_is_not_touched(self, asset, caller):
        job, record, client = _with(asset, caller)
        UnrealMeasurement.objects.filter(pk=record.pk).update(verdict='does_not_fit', lower_bound_mb=8102)
        accepted(client, job, self_spill(record, job_peak_mb=9900), result='does_not_fit')
        assert state(record)[:5] == ('does_not_fit', 'pilot', '', None, 8102)


SPILL_BOUNDS = [(9000, 8500, 9000), (None, 9500, 9500), (9000, None, 9000), (None, None, 8102)]


@pytest.mark.django_db
class TestSpill:

    @pytest.mark.parametrize('raised,job_peak,bound', SPILL_BOUNDS, ids=[repr(c) for c in SPILL_BOUNDS])
    def test_self_spill_does_not_fit(self, asset, caller, raised, job_peak, bound):
        job, record, client = _with(asset, caller, job_peak_max_mb=raised)
        accepted(client, job, self_spill(record, job_peak_mb=job_peak), result='does_not_fit')
        assert state(record)[:5] == ('does_not_fit', 'spill', '', max(raised or 0, job_peak or 0) or None, bound)

    @pytest.mark.parametrize('body', [contention, report_body], ids=['contention', 'no-spill'])
    def test_no_self_spill_keeps_fits(self, rendering, body):
        job, record, _worker, client = rendering
        accepted(client, job, body(record))
        assert state(record)[:5] == ('fits', 'pilot', '', 4000, None)

    def test_an_unmeasured_spill_keeps_fits(self, rendering):
        job, record, _worker, client = rendering
        accepted(client, job, report_body(record, spilled=None, shared_peak_mb=None))
        assert state(record)[0] == 'fits'

    def test_self_spill_wins_over_the_job_peak_source(self, rendering):
        job, record, _worker, client = rendering
        over = LARGEST_FITS_PEAK + 1
        accepted(client, job, self_spill(record, job_peak_mb=over), result='does_not_fit')
        assert state(record)[:5] == ('does_not_fit', 'spill', '', over, over)

    def test_a_standalone_job_adds_no_warning(self, rendering):
        job, record, _worker, client = rendering
        accepted(client, job, self_spill(record), result='does_not_fit')
        assert job.sequence_submission_id is None
        assert not SequenceSubmission.objects.exclude(warnings=[]).exists()


@pytest.mark.django_db
class TestCovering:

    @pytest.fixture
    def derived(self, asset, caller):
        worker, client = caller
        covering = keyed_record(asset, 'c1' * 32, worker, peak=9800)
        job, record = rendering_render(asset, worker, client)
        return job, derive_from(record, covering), covering, client

    def test_self_spill_marks_the_covering_record_too(self, derived):
        job, record, covering, client = derived
        accepted(client, job, self_spill(record, job_peak_mb=8500), result='does_not_fit')
        assert state(record)[:5] == ('does_not_fit', 'spill', '', 8500, 8500)
        # Its own result peak (9,800) is larger than the bound, so its lower bound keeps it.
        assert state(covering)[:5] == ('does_not_fit', 'spill', '', None, 9800)

    def test_a_job_peak_over_the_line_marks_the_covering_record(self, derived):
        job, record, covering, client = derived
        over = LARGEST_FITS_PEAK + 1
        accepted(client, job, report_body(record, job_peak_mb=over), result='does_not_fit')
        assert state(covering)[:5] == ('does_not_fit', 'job_peak', '', None, over)

    def test_contention_leaves_both_fits(self, derived):
        job, record, covering, client = derived
        accepted(client, job, contention(record))
        assert (state(record)[0], state(covering)[0]) == ('fits', 'fits')

    @pytest.mark.parametrize('change', [{'current': False}, {'verdict': 'does_not_fit'}],
                             ids=['not-current', 'does-not-fit'])
    def test_a_covering_record_no_longer_fits_is_not_touched(self, derived, change):
        job, record, covering, client = derived
        UnrealMeasurement.objects.filter(pk=covering.pk).update(**change)
        before = state(covering)
        accepted(client, job, self_spill(record), result='does_not_fit')
        assert state(covering) == before

    @pytest.mark.parametrize('owner', ['other-worker', 'other-kind'])
    def test_a_covering_record_of_another_pair_is_left_alone(self, asset, caller, make_worker_client, mocker, owner):
        worker, client = caller
        if owner == 'other-worker':
            other = kinds_worker(make_worker_client('mt-cover-other')[0], KIND)
            covering = keyed_record(asset, 'c4' * 32, other)
        else:
            covering = keyed_record(asset, 'c4' * 32, worker, KIND_AMD)
        job, record = rendering_render(asset, worker, client)
        derive_from(record, covering)
        logger = mocker.patch('workers.services.memory_report.logger')
        before = state(covering)
        accepted(client, job, self_spill(record), result='does_not_fit')
        assert state(record)[:2] == ('does_not_fit', 'spill')
        assert state(covering) == before
        assert [c[0] for c in logger.method_calls] == ['warning']


@pytest.mark.django_db
class TestFitsNowhereHook:

    def test_registered_on_commit_for_the_records_key(self, rendering, mocker, django_capture_on_commit_callbacks):
        job, record, _worker, client = rendering
        check = mocker.patch(CHECK)
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            accepted(client, job, self_spill(record), result='does_not_fit')
        check.assert_not_called()
        for callback in callbacks:
            callback()
        check.assert_called_once_with(*_key(record))

    @pytest.mark.parametrize('body', [report_body, contention], ids=['raised-peak', 'contention'])
    def test_no_verdict_change_registers_nothing(self, rendering, mocker, body,
                                                 django_capture_on_commit_callbacks):
        job, record, _worker, client = rendering
        check = mocker.patch(CHECK)
        with django_capture_on_commit_callbacks(execute=True):
            accepted(client, job, body(record, job_peak_mb=9000))
        assert state(record)[3] == 9000
        check.assert_not_called()

    def test_a_changed_covering_record_is_checked_too(self, asset, caller, mocker,
                                                      django_capture_on_commit_callbacks):
        worker, client = caller
        covering = keyed_record(asset, 'c2' * 32, worker)
        job, record = rendering_render(asset, worker, client)
        derive_from(record, covering)
        check = mocker.patch(CHECK)
        with django_capture_on_commit_callbacks(execute=True):
            accepted(client, job, self_spill(record), result='does_not_fit')
        assert [c.args for c in check.call_args_list] == [_key(record), _key(covering)]

    def test_an_unchanged_covering_record_is_not_checked(self, asset, caller, mocker,
                                                         django_capture_on_commit_callbacks):
        worker, client = caller
        covering = keyed_record(asset, 'c3' * 32, worker, verdict='does_not_fit')
        job, record = rendering_render(asset, worker, client)
        derive_from(record, covering)
        check = mocker.patch(CHECK)
        with django_capture_on_commit_callbacks(execute=True):
            accepted(client, job, self_spill(record), result='does_not_fit')
        check.assert_called_once_with(*_key(record))
