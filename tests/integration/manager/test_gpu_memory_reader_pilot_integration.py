# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the pilot result's GPU cross-check by card key
(gpu-memory-reader FR-GH-10, FR-GN-5, FR-GT-5, AC-10).

The worker's reading reaches the manager through the real heartbeat and the
result body is the worker's own ``pilot.report_body``; the manager must match
the GPU by ``card_key`` and total, never by ``index``.
"""

import pytest

from tests.unit.manager.card_report_helpers import card, stored
from workers.models import Worker

from .gpu_memory_reader_helpers import (
    AMD_IDS, AMD_KEY, AMD_KIND, AMD_NAME, AMD_TOTAL, NV_KEY, NV_NAME, NV_TOTAL,
    amd_gpu, nv_gpu, payload_of, reading, report, send_heartbeat,
)
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import claim, created_job, pilots, post_result, record_of

MISMATCH = {'gpu': ["Does not match this worker's reported GPU memory."]}
DONE = {'status': 'DONE', 'peak_mb': 8102}


def _rendering_on(admin_client, worker_with_token, ticked):
    """A pilot claimed by a worker whose heartbeat reported one NV-like and one AMD-like card.

    Per-machine D5: only the ``ticked`` card counts, so the key gets one
    pilot for this worker, of that card's kind; the other card is stored
    un-ticked (its kind is still known, FR-MT-V-3).
    """
    worker, client = worker_with_token
    send_heartbeat(client, worker, payload_of(reading(nv_gpu(), amd_gpu(), source='mixed')))
    cards = [card(key=NV_KEY, ticked=ticked == NV_KEY), card(key=AMD_KEY, ticked=ticked == AMD_KEY, **AMD_IDS)]
    Worker.objects.filter(pk=worker.pk).update(card_status=stored(cards=cards))
    created_job(admin_client, unreal_asset())
    [job] = pilots()
    assert claim(client, job, worker).status_code == 200
    return job, worker, client


@pytest.fixture
def rendering(admin_client, worker_with_token):
    """A pilot of the NV-like card's kind, claimed."""
    return _rendering_on(admin_client, worker_with_token, NV_KEY)


@pytest.fixture
def rendering_amd(admin_client, worker_with_token):
    """A pilot of the AMD-like card's kind, claimed."""
    return _rendering_on(admin_client, worker_with_token, AMD_KEY)


def _assert_untouched(job):
    job.refresh_from_db()
    assert job.status == 'RENDERING'
    assert record_of(job).peak_mb is None


@pytest.mark.django_db
class TestPilotResultByCardKeyIntegration:

    def test_worker_report_with_its_own_card_key_is_stored(self, rendering):
        job, worker, client = rendering
        body = report(nv_gpu())
        assert body['gpu'] == {'index': 0, 'name': NV_NAME, 'total_mb': NV_TOTAL, 'card_key': NV_KEY}  # guard
        resp = post_result(client, job.pk, body)
        assert (resp.status_code, resp.data) == (200, DONE)
        job.refresh_from_db()
        assert job.status == 'DONE'
        record = record_of(job)
        assert (record.peak_mb, record.worker_id, record.gpu_index) == (8102, worker.pk, 0)
        assert (record.gpu_name, record.gpu_total_mb) == (NV_NAME, NV_TOTAL)

    def test_second_brand_card_is_matched_by_its_key(self, rendering_amd):
        job, _worker, client = rendering_amd
        resp = post_result(client, job.pk, report(amd_gpu(), card_kind=AMD_KIND))
        assert (resp.status_code, resp.data) == (200, DONE)
        record = record_of(job)
        assert (record.gpu_index, record.gpu_name, record.gpu_total_mb) == (1, AMD_NAME, AMD_TOTAL)

    def test_same_index_with_another_key_is_refused(self, rendering):
        job, _worker, client = rendering
        resp = post_result(client, job.pk, report(nv_gpu(card_key='pci:0000:0b:00.0')))
        # No stored card has that key either, so the card kind check refuses it too (FR-MT-V-3).
        assert (resp.status_code, resp.data['gpu']) == (400, MISMATCH['gpu'])
        _assert_untouched(job)

    def test_same_index_with_the_other_cards_key_is_refused_on_total(self, rendering):
        # Index 0 carries the AMD-like key but the NV-like total: no stored card has both.
        job, _worker, client = rendering
        resp = post_result(client, job.pk, report(nv_gpu(card_key=AMD_KEY)))
        # The AMD-like key's stored card is of another kind, so the card kind check refuses it too.
        assert (resp.status_code, resp.data['gpu']) == (400, MISMATCH['gpu'])
        _assert_untouched(job)

    def test_another_index_with_the_same_key_is_accepted(self, rendering):
        # FR-GN-5: Unreal's adapter index need not be the reading's index.
        job, _worker, client = rendering
        resp = post_result(client, job.pk, report(nv_gpu(index=5)))
        assert (resp.status_code, resp.data) == (200, DONE)
        record = record_of(job)
        assert (record.gpu_index, record.gpu_total_mb, record.peak_mb) == (5, NV_TOTAL, 8102)

    @pytest.mark.parametrize('card_key', ['missing', None, 'apple-gpu', 'PCI:0000:0A:00.0', 7])
    def test_missing_or_malformed_card_key_is_400(self, rendering, card_key):
        job, _worker, client = rendering
        body = report(nv_gpu())
        if card_key == 'missing':
            del body['gpu']['card_key']
        else:
            body['gpu']['card_key'] = card_key
        resp = post_result(client, job.pk, body)
        assert resp.status_code == 400 and 'gpu' in resp.data, resp.data
        assert 'card_key' in resp.data['gpu'], resp.data
        _assert_untouched(job)

    @pytest.mark.parametrize('extra, keys', [
        ({'uuid': 'GPU-0'}, 'gpu.uuid'),
        ({'pci_bus_id': '00000000:0A:00.0', 'vendor_id': 4318}, 'gpu.pci_bus_id, gpu.vendor_id'),
    ])
    def test_strict_key_check_still_refuses_unknown_gpu_keys(self, rendering, extra, keys):
        job, _worker, client = rendering
        body = report(nv_gpu())
        body['gpu'].update(extra)
        resp = post_result(client, job.pk, body)
        assert (resp.status_code, resp.data) == (400, {'detail': f'Unknown keys: {keys}.'})
        _assert_untouched(job)


@pytest.mark.django_db
def test_a_reading_that_lost_its_card_after_the_claim_refuses_the_result(rendering):
    """The cross-check uses the stored capability: an override heartbeat after the claim fails it."""
    job, worker, client = rendering
    send_heartbeat(client, worker, payload_of(reading(nv_gpu(), source='override')))
    resp = post_result(client, job.pk, report(nv_gpu()))
    assert (resp.status_code, resp.data) == (400, MISMATCH)
    _assert_untouched(job)
