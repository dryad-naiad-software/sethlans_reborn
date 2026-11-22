# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: ``pilot-result/``'s job-dependent per-machine checks and
the stored verdict (Unreal per-machine memory test FR-MT-V-3, D6, AC-4).

Covers: the reported ``card_kind`` must be the job's ``unreal.card_kind``
and the kind of the caller's stored card ``gpu.card_key`` (400 otherwise;
a stored card that is un-ticked or not ``ok`` still has its kind); D6
recomputed from ``peak_mb + baseline_used_mb`` against
``floor(total * 0.93)``: ``fits`` over the line is 400, ``does_not_fit``
under or at the line is accepted (the manager cannot see the samples),
``fits`` exactly at the line is accepted; a ``does_not_fit`` pilot ends
DONE with ``verdict_source='pilot'`` and the key stays offered to another
machine with a fits result; the fits-nowhere check is registered
``on_commit`` with the record's key and never inline or on a refusal.
"""

import pytest

from shared.unreal.validation import overrides_sha256

from workers.models import Job
from workers.services import pilot_result

from .machine_test_helpers import DOES_NOT_FIT, KIND_AMD, KIND_OTHER, kind_card, set_cards
from .machine_verdict_helpers import (
    BASELINE, LARGEST_FITS_PEAK, LINE, assert_untouched, dnf_body, peak_body, poll_ids, state_of,
)
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import (
    KIND, claim, claimed_pilot, fits_record, pilot_body, post_result, ready_worker, record_of,
)

CHECK = 'workers.services.fits_nowhere.check'


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.fixture
def pilot(admin_client, asset, worker_with_token):
    worker, client = worker_with_token
    return claimed_pilot(admin_client, asset, worker, client), worker, client


@pytest.mark.django_db
class TestCardKind:

    def test_the_pilot_carries_its_pair_kind(self, pilot):
        job, _worker, _client = pilot
        assert job.render_settings['unreal.card_kind'] == KIND == record_of(job).card_kind

    @pytest.mark.parametrize('kind', [KIND_AMD, KIND_OTHER, '10de:2b85:none'])
    def test_another_kind_than_the_jobs_is_400(self, pilot, kind):
        job, _worker, client = pilot
        resp = post_result(client, job.pk, pilot_body(card_kind=kind))
        assert (resp.status_code, resp.data) == (400, {'card_kind': [pilot_result.KIND_NOT_THE_JOBS]})
        assert_untouched(job)

    def test_a_job_without_a_card_kind_is_400(self, pilot):
        job, _worker, client = pilot
        settings = {k: v for k, v in job.render_settings.items() if k != 'unreal.card_kind'}
        Job.objects.filter(pk=job.pk).update(render_settings=settings)
        resp = post_result(client, job.pk, pilot_body())
        assert (resp.status_code, resp.data) == (400, {'card_kind': [pilot_result.KIND_NOT_THE_JOBS]})

    @pytest.mark.parametrize('cards', [
        [kind_card(KIND_AMD, 0)], [kind_card(KIND, 1)], [],
    ], ids=['stored-card-of-another-kind', 'kind-on-another-card-key', 'no-stored-cards'])
    def test_the_stored_cards_kind_must_match(self, pilot, cards):
        job, worker, client = pilot
        set_cards(worker, cards)
        resp = post_result(client, job.pk, pilot_body())
        assert (resp.status_code, resp.data) == (400, {'card_kind': [pilot_result.KIND_NOT_THE_CARDS]})
        assert_untouched(job)

    @pytest.mark.parametrize('overrides', [{'ticked': False}, {'state': 'lost'}], ids=['unticked', 'lost'])
    def test_a_stored_card_keeps_its_kind_whatever_its_tick(self, pilot, overrides):
        job, worker, client = pilot
        set_cards(worker, [kind_card(KIND, 0, **overrides)])
        assert post_result(client, job.pk, pilot_body()).status_code == 200
        assert record_of(job).card_kind == KIND


@pytest.mark.django_db
class TestVerdictRecomputed:

    def test_fits_over_the_line_is_400(self, pilot):
        job, _worker, client = pilot
        resp = post_result(client, job.pk, peak_body(LARGEST_FITS_PEAK + 1))
        assert resp.status_code == 400 and list(resp.data) == ['verdict'], resp.data
        [message] = resp.data['verdict']
        assert message.startswith("Must be does_not_fit") and f"({LINE} MB)" in message
        assert_untouched(job)

    def test_fits_exactly_at_the_line_is_accepted(self, pilot):
        job, _worker, client = pilot
        assert LARGEST_FITS_PEAK + BASELINE == LINE
        assert post_result(client, job.pk, peak_body(LARGEST_FITS_PEAK)).status_code == 200
        assert (record_of(job).verdict, record_of(job).peak_mb) == ('fits', LARGEST_FITS_PEAK)

    @pytest.mark.parametrize('peak', [8102, LARGEST_FITS_PEAK, LARGEST_FITS_PEAK + 1], ids=['under', 'at', 'over'])
    def test_does_not_fit_is_accepted_anywhere(self, pilot, peak):
        # A saturated sample the manager cannot see may turn fits into does_not_fit, never back.
        job, _worker, client = pilot
        resp = post_result(client, job.pk, peak_body(peak, verdict='does_not_fit'))
        assert resp.status_code == 200, resp.data
        record = record_of(job)
        assert (record.verdict, record.lower_bound_mb, record.peak_mb) == ('does_not_fit', peak, peak)

    def test_kind_and_verdict_errors_come_together(self, pilot):
        job, _worker, client = pilot
        resp = post_result(client, job.pk, peak_body(LARGEST_FITS_PEAK + 1, card_kind=KIND_AMD))
        assert resp.status_code == 400 and set(resp.data) == {'card_kind', 'verdict'}, resp.data


@pytest.mark.django_db
class TestDoesNotFit:

    def test_ends_done_with_the_pilot_as_source(self, pilot):
        job, worker, client = pilot
        resp = post_result(client, job.pk, dnf_body())
        assert (resp.status_code, resp.data) == (200, {'status': 'DONE', 'peak_mb': 8102})
        job.refresh_from_db()
        assert (job.status, job.error_message) == ('DONE', '')
        record = record_of(job)
        assert (record.verdict, record.verdict_source, record.lower_bound_mb) == ('does_not_fit', 'pilot', 8102)
        assert (record.current, record.stale_reason, record.worker_id) == (True, '', worker.pk)
        assert state_of(record) == 'does_not_fit'

    def test_the_key_stays_offered_to_another_machine_that_fits(self, pilot, make_worker_client):
        job, worker, client = pilot
        normal = Job.objects.get(name='Still01')
        other, other_client, _token = make_worker_client('mt-dnf-other')
        ready_worker(other)
        fits_record(normal.asset, normal.unreal_target_sha256, other)
        assert post_result(client, job.pk, dnf_body()).status_code == 200
        normal.refresh_from_db()
        assert normal.status == 'QUEUED'
        assert normal.pk not in poll_ids(client)
        assert claim(client, normal, worker).data == {'error': DOES_NOT_FIT}
        assert normal.pk in poll_ids(other_client)
        assert claim(other_client, normal, other).status_code == 200


@pytest.mark.django_db
class TestFitsNowhereHook:

    @pytest.mark.parametrize('body', [pilot_body(), dnf_body()], ids=['fits', 'does_not_fit'])
    def test_registered_on_commit_with_the_key(self, pilot, mocker, django_capture_on_commit_callbacks, body):
        job, _worker, client = pilot
        check = mocker.patch(CHECK, return_value=0)
        with django_capture_on_commit_callbacks(execute=False) as callbacks:
            assert post_result(client, job.pk, body).status_code == 200
        check.assert_not_called()
        for callback in callbacks:
            callback()
        record = record_of(job)
        check.assert_called_once_with(record.project_id, record.asset_id, record.overrides_sha256,
                                      record.target_sha256)

    def test_not_registered_on_a_400_or_409(self, pilot, mocker, django_capture_on_commit_callbacks):
        job, _worker, client = pilot
        check = mocker.patch(CHECK, return_value=0)
        with django_capture_on_commit_callbacks(execute=True):
            assert post_result(client, job.pk, pilot_body(card_kind=KIND_AMD)).status_code == 400
            assert post_result(client, job.pk, peak_body(LARGEST_FITS_PEAK + 1)).status_code == 400
            # The CVar mismatch fails the pilot (409); a repeat then is not rendering here (409).
            mismatch = pilot_body(overrides_sha256=overrides_sha256({'a.b': '1'}))
            assert post_result(client, job.pk, mismatch).status_code == 409
            assert post_result(client, job.pk, pilot_body()).status_code == 409
        check.assert_not_called()
        job.refresh_from_db()
        assert job.status == 'ERROR' and record_of(job).verdict == ''

    def test_a_crash_in_the_check_does_not_undo_the_result(self, pilot, mocker, django_capture_on_commit_callbacks):
        job, _worker, client = pilot
        mocker.patch(CHECK, side_effect=RuntimeError('boom'))
        with django_capture_on_commit_callbacks(execute=True):
            resp = post_result(client, job.pk, pilot_body())
        assert resp.status_code == 200
        job.refresh_from_db()
        assert job.status == 'DONE' and record_of(job).verdict == 'fits'
