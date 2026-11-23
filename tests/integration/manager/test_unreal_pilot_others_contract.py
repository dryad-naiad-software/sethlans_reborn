# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Contract: a pilot whose peak carries other programs' use (owner decision Q10, band 2) passes the manager's own
``pilot-result/`` checks unchanged.

The worker adds the part of the highest other-programs figure that ``peak + baseline`` does not already hold
(``pilot_others``) to the figure that set the peak, so the manager's ``peak_mb == max(card_delta_peak_mb,
csv_local_used_peak_mb)``, ``peak_source``, D6 recompute, ``lower_bound_mb`` and card-total checks see a
consistent body. The worker releases a band-2 pilot only when its adjusted card delta plus baseline is more than
the card's ``gpu.total_mb`` (``pilot_flow.check_over_card``), the manager's own bound for ``does_not_fit``: the
last case shows a card figure past it is refused with 400, while a CSV peak above the card is accepted.
"""

import json

import pytest

from shared.unreal.validation import overrides_sha256
from sethlans_worker_agent.gpu_memory.types import make_gpu
from sethlans_worker_agent.unreal_executor import pilot
from tests.unit.worker.unreal._executor_fakes import phase_run, trace

from .unreal_helpers import BUILD, card_key_of, unreal_asset
from .unreal_pilot_helpers import DRIVER, KIND, claimed_pilot, post_result, record_of

TOTAL = 32607                     # the card ``ready_worker`` reports; its 93% line is 30324
BASELINE = 1000


def _report(max_used, csv, top):
    """The worker's own body: a render that peaked at ``max_used`` holding the 1000 MB baseline of others' use,
    other programs having used up to ``top`` MB at some other moment."""
    run = phase_run('render', {0: BASELINE}, {0: BASELINE}, trace({0: max_used}, total=TOTAL))
    run.outcome.vram.max_others, run.outcome.vram.others_at_max = {0: top}, {0: BASELINE}
    metrics = pilot.measure([run], csv, top)
    gpu = make_gpu(0, 'GPU', TOTAL, 0, 0, card_key=card_key_of(0))
    body = pilot.report_body(metrics, gpu, prep_s=24.1, warmup_s=800.2, render_s=1380.5, frames=23,
                             engine_build=BUILD, overrides_sha256=overrides_sha256({}), summary='pilot_frames=23',
                             card_kind=KIND, driver_version=DRIVER)
    return metrics, json.loads(json.dumps(body))


@pytest.fixture
def ready_pilot(db, admin_client, worker_with_token):
    worker, client = worker_with_token
    return claimed_pilot(admin_client, unreal_asset(), worker, client), client


@pytest.mark.django_db
class TestPilotOthersAddedIntegration:

    @pytest.mark.parametrize('max_used,csv,top,added,verdict', [
        (21000, None, 4000, 3000, 'fits'),                # card peak 20000 + 3000, under the line
        (29000, None, 2500, 1500, 'does_not_fit'),        # 28000 + 1000 fits; with 1500 added it is over the line
        (26000, 29500, 3000, 2000, 'does_not_fit'),       # CSV peak carries it: 31500, card delta 25000 as read
        (29000, None, TOTAL - 28000, TOTAL - BASELINE - 28000, 'does_not_fit'),  # card figure + baseline == total
        (26000, 31000, 3000, 2000, 'does_not_fit'),       # CSV peak 33000 + 1000 is above the card: still reported
    ], ids=['card_fits', 'card_flips_to_does_not_fit', 'csv_does_not_fit', 'card_exactly_the_total',
            'csv_above_the_card'])
    def test_an_adjusted_peak_is_accepted_and_stored(self, ready_pilot, max_used, csv, top, added, verdict):
        job, client = ready_pilot
        metrics, body = _report(max_used, csv, top)
        assert metrics.others_added_mb == added and metrics.verdict == verdict
        assert metrics.card_delta_peak_mb + metrics.baseline_used_mb <= TOTAL  # what the worker's release rule allows
        resp = post_result(client, job.pk, body)
        assert resp.status_code == 200, resp.data
        record = record_of(job)
        assert (record.peak_mb, record.verdict, record.lower_bound_mb) == \
            (metrics.peak_mb, verdict, metrics.peak_mb if verdict == 'does_not_fit' else None)
        assert record.peak_mb == max(record.card_delta_peak_mb, record.csv_local_used_peak_mb or 0)

    def test_a_csv_peak_above_the_card_is_a_does_not_fit_the_manager_stores(self, ready_pilot):
        job, client = ready_pilot
        metrics, body = _report(26000, 31000, 3000)
        assert (metrics.peak_source, metrics.peak_mb + metrics.baseline_used_mb > TOTAL) == ('csv', True)
        assert post_result(client, job.pk, body).status_code == 200
        assert (record_of(job).verdict, record_of(job).lower_bound_mb) == ('does_not_fit', 33000)

    def test_a_card_figure_past_the_card_total_would_be_refused(self, ready_pilot):
        """Why the worker releases instead: 65 MB past the total is outside the manager's 64 MB skew."""
        job, client = ready_pilot
        _, body = _report(29000, None, TOTAL - 28000 + 65)
        resp = post_result(client, job.pk, body)
        assert resp.status_code == 400 and 'card_delta_peak_mb' in resp.data
