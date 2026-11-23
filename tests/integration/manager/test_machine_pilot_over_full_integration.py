# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: an over-full memory test result whose peak is above the
card's total (GitHub issue #290; Unreal per-machine memory test D6).

When a render over-fills its card, Unreal's CSV LocalUsed peak (and so
``peak_mb`` and the ``does_not_fit`` lower bound) can be above
``gpu.total_mb``: part of the allocation is already in shared memory.
``POST /api/jobs/{id}/pilot-result/`` must accept such a ``does_not_fit``
result (bounded only by ``MAX_PEAK_MB``), store it, finish the pilot and
register the fits-nowhere check on commit; the stop message and the
machine row then show a lower bound above the card's total.

Still refused: the same figures with the verdict ``fits`` (400 on
``peak_mb`` and ``csv_local_used_peak_mb``, the cap stays); a
``does_not_fit`` card delta above the total (serializer, 400); a
``does_not_fit`` card delta plus baseline above total + 64 (service, 400
on ``card_delta_peak_mb``); a CSV peak above ``MAX_PEAK_MB``.

The real figures of #290: peak = CSV = lower bound 23,266 MB on a
20,420 MB card; the same ratio on ``pilot_body()``'s 32,607 MB card is
37,151 MB.
"""

import pytest

from shared.unreal.vram_gate import MAX_PEAK_MB

from workers.models import Job

from .fits_nowhere_helpers import error_of, statuses, stop_text
from .machine_rows_helpers import machines, row_of
from .machine_verdict_helpers import assert_untouched, fresh
from .unreal_helpers import card_key_of, unreal_asset
from .unreal_pilot_helpers import KIND, claimed_pilot, live_card, pilot_body, post_result, record_of

NAME = 'Example GPU 20'
# (card total, CSV LocalUsed peak above it): #290's real run, and the same ratio on the default card.
CARDS = [(20420, 23266), (32607, 37151)]
CARD_IDS = ['issue-290-20420', 'default-32607']
CARD_DELTA = 18900
BASELINE = 1200
SKEW = 64
CARD_TOTAL_MESSAGE = "card_delta_peak_mb + baseline_used_mb exceeds the card's total memory."


def over_body(total, csv, card=CARD_DELTA, baseline=BASELINE, verdict='does_not_fit', **overrides):
    """A ``pilot-result/`` body on a ``total`` MB card whose CSV peak is ``csv`` (peak = max of the two)."""
    peak = max(card, csv)
    dnf = verdict == 'does_not_fit'
    body = pilot_body(
        peak_mb=peak, peak_source='csv' if csv > card else 'card', card_delta_peak_mb=card,
        csv_local_used_peak_mb=csv, baseline_used_mb=baseline, verdict=verdict,
        lower_bound_mb=peak if dnf else None, shared_peak_mb=max(csv - total, 0) if dnf else None,
        gpu={'index': 0, 'name': NAME, 'total_mb': total, 'card_key': card_key_of(0)},
    )
    body.update(overrides)
    return body


def cap_message(total):
    return [f"Must not exceed gpu.total_mb ({total})."]


@pytest.fixture
def asset(db):
    return unreal_asset()


@pytest.fixture
def over_pilot(admin_client, asset, worker_with_token):
    """Claim the pilot of a new job, then give the worker a live card of ``total`` MB; ``(pilot, worker, client)``."""
    def _make(total):
        worker, client = worker_with_token
        pilot = claimed_pilot(admin_client, asset, worker, client)
        live_card(worker, total_mb=total)
        return pilot, worker, client
    return _make


@pytest.mark.django_db
class TestOverFullDoesNotFitIsAccepted:
    """#290: a ``does_not_fit`` peak and CSV peak above ``gpu.total_mb`` are stored, not refused."""

    @pytest.mark.parametrize('total, csv', CARDS, ids=CARD_IDS)
    def test_the_result_is_stored_and_the_pilot_is_done(self, over_pilot, total, csv):
        job, worker, client = over_pilot(total)
        resp = post_result(client, job.pk, over_body(total, csv))
        assert (resp.status_code, resp.data) == (200, {'status': 'DONE', 'peak_mb': csv}), resp.data
        job.refresh_from_db()
        assert (job.status, job.completed_at is not None) == ('DONE', True)
        record = record_of(job)
        assert (record.verdict, record.verdict_source) == ('does_not_fit', 'pilot')
        assert (record.peak_mb, record.peak_source, record.csv_local_used_peak_mb) == (csv, 'csv', csv)
        assert (record.lower_bound_mb, record.card_delta_peak_mb, record.baseline_used_mb) == (csv, CARD_DELTA, BASELINE)
        assert (record.gpu_total_mb, record.gpu_name, record.shared_peak_mb) == (total, NAME, csv - total)
        assert (record.worker_id, record.card_kind) == (worker.pk, KIND)
        assert record.lower_bound_mb > record.gpu_total_mb

    def test_the_fits_nowhere_check_is_registered_on_commit(self, over_pilot, django_capture_on_commit_callbacks):
        """#290: the check runs after the commit and stops the key with the over-total lower bound."""
        total, csv = CARDS[0]
        job, worker, client = over_pilot(total)
        normal = Job.objects.get(name='Still01')
        with django_capture_on_commit_callbacks() as callbacks:
            assert post_result(client, job.pk, over_body(total, csv)).status_code == 200
        assert callbacks, "the pilot result registered no on-commit callback"
        assert statuses(job, normal) == ['DONE', 'QUEUED']  # not inline
        for callback in callbacks:
            callback()
        assert statuses(job, normal) == ['DONE', 'ERROR']
        assert error_of(normal) == stop_text(NAME, total, worker.hostname, csv)
        assert f"({total} MB)" in error_of(normal) and f"at least {csv} MB" in error_of(normal)

    def test_the_machine_row_shows_a_lower_bound_above_the_card_total(
            self, admin_client, over_pilot, django_capture_on_commit_callbacks):
        """#290: the job's machine list keeps the over-total figures for the machine."""
        total, csv = CARDS[0]
        job, worker, client = over_pilot(total)
        with django_capture_on_commit_callbacks(execute=True):
            assert post_result(client, job.pk, over_body(total, csv)).status_code == 200
        row = row_of(machines(admin_client, Job.objects.get(name='Still01')), worker)
        assert (row['state'], row['peak_mb'], row['lower_bound_mb']) == ('does_not_fit', csv, csv)
        assert (row['card_name'], row['card_total_mb']) == (NAME, total)
        assert row['lower_bound_mb'] > row['card_total_mb']

    def test_a_peak_of_exactly_max_peak_mb_is_accepted(self, over_pilot):
        """#290: above the card's total, ``does_not_fit`` figures are bounded by ``MAX_PEAK_MB`` only."""
        total = CARDS[0][0]
        job, _worker, client = over_pilot(total)
        resp = post_result(client, job.pk, over_body(total, MAX_PEAK_MB))
        assert resp.status_code == 200, resp.data
        assert record_of(job).lower_bound_mb == MAX_PEAK_MB

    def test_card_delta_plus_baseline_at_total_plus_64_is_accepted(self, over_pilot):
        """#290: the card's own reading may reach the total plus the 64 MB skew."""
        total, csv = CARDS[0]
        job, _worker, client = over_pilot(total)
        body = over_body(total, csv, card=19800, baseline=total + SKEW - 19800)
        assert post_result(client, job.pk, body).status_code == 200


@pytest.mark.django_db
class TestOverFullFitsIsRefused:
    """#290: the cap stays for ``fits``: the same over-total figures are refused."""

    @pytest.mark.parametrize('total, csv', CARDS, ids=CARD_IDS)
    def test_400_on_peak_and_csv_peak(self, over_pilot, total, csv):
        job, _worker, client = over_pilot(total)
        resp = post_result(client, job.pk, over_body(total, csv, verdict='fits'))
        assert (resp.status_code, resp.data) == (400, {
            'peak_mb': cap_message(total), 'csv_local_used_peak_mb': cap_message(total)})
        assert_untouched(job)


@pytest.mark.django_db
class TestOverFullDoesNotFitStillChecked:
    """#290: the dedicated-memory card reading of a ``does_not_fit`` result still fits in the card."""

    def test_card_delta_plus_baseline_above_total_plus_64_is_400(self, over_pilot):
        total, csv = CARDS[0]
        job, _worker, client = over_pilot(total)
        body = over_body(total, csv, card=19800, baseline=total + SKEW + 1 - 19800)
        resp = post_result(client, job.pk, body)
        assert (resp.status_code, resp.data) == (400, {'card_delta_peak_mb': [CARD_TOTAL_MESSAGE]})
        assert_untouched(job)

    def test_card_delta_above_the_total_is_refused(self, over_pilot):
        total, csv = CARDS[0]
        job, _worker, client = over_pilot(total)
        resp = post_result(client, job.pk, over_body(total, csv, card=total + 1, baseline=0))
        assert (resp.status_code, resp.data) == (400, {'card_delta_peak_mb': cap_message(total)})
        assert_untouched(job)

    @pytest.mark.parametrize('field', ['baseline_used_mb', 'baseline_drift_mb'])
    def test_baseline_figures_above_the_total_are_refused(self, over_pilot, field):
        total, csv = CARDS[0]
        job, _worker, client = over_pilot(total)
        resp = post_result(client, job.pk, over_body(total, csv, **{field: total + 1}))
        assert resp.status_code == 400 and resp.data.get(field) == cap_message(total), resp.data
        assert_untouched(job)

    def test_a_csv_peak_above_max_peak_mb_is_refused(self, over_pilot):
        total = CARDS[0][0]
        job, _worker, client = over_pilot(total)
        resp = post_result(client, job.pk, over_body(total, MAX_PEAK_MB + 1))
        assert resp.status_code == 400 and 'csv_local_used_peak_mb' in resp.data, resp.data
        assert_untouched(job)
        assert fresh(record_of(job)).lower_bound_mb is None
