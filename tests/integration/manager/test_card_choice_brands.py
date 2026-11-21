# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the brand rule on the manager (Unreal render per card
D7, FR-PC-BR-5, AC-6 manager part): ``POST /api/heartbeat/{pk}/card-choice/``
refuses ticked cards of more than one brand with 400 and the "not
supported" sentence, from the reported ``vendor_id`` and ``backend``; one
brand is accepted, also on a machine that has two; and the worker's
refusal with code ``mixed_brands`` (its check 6b) is accepted as an outcome.
"""

import pytest

from tests.unit.manager.card_report_helpers import KEY, KEY_2, card
from workers.services import card_choice
from workers.services.card_brand import MIXED_BRANDS_TEXT
from workers.views import heartbeat_card_outcome

from .card_choice_helpers import (
    AMD, HB, NVIDIA, NVIDIA_2, SECOND_NVIDIA, body, outcome, reported, row, two_cards, url,
)

NULL_IDS = {'vendor_id': None, 'device_id': None}
MIXED_REASON = "the requested cards are of more than one brand (not supported)"


def _ticks(*pairs):
    """The action's ``cards`` for ``(key, ids)`` pairs."""
    return [{'key': key, **ids} for key, ids in pairs]


def _post(admin_client, worker, cards, **overrides):
    return admin_client.post(url(worker.pk), body(cards=cards, **overrides), format='json')


def _assert_mixed(resp, worker, brands):
    assert resp.status_code == 400, resp.content
    assert resp.json() == {'detail': MIXED_BRANDS_TEXT.format(brands=brands)}
    stored = row(worker)
    assert (stored.card_choice_request, stored.card_choice_issued) == ({}, [])


@pytest.fixture(autouse=True)
def _fresh_warnings(monkeypatch):
    monkeypatch.setattr(heartbeat_card_outcome, '_warned_unknown', {})


@pytest.mark.django_db
class TestMixedBrandsRefused:

    @pytest.mark.parametrize('gpu_mode', ['split', 'combined'])
    @pytest.mark.parametrize('method', ['gpu', 'cpu_gpu', 'cpu'])
    def test_nvidia_and_amd_ticked_together_is_400(self, admin_client, make_worker_client, method, gpu_mode):
        """AC-6: the default report is a machine with an NVIDIA card and AMD integrated graphics."""
        worker, _client = reported(make_worker_client, f'mixed-{method}-{gpu_mode}')
        resp = _post(admin_client, worker, _ticks((KEY, NVIDIA), (KEY_2, AMD)), method=method, gpu_mode=gpu_mode)
        _assert_mixed(resp, worker, 'AMD, NVIDIA')

    def test_refusal_is_logged_without_the_body(self, admin_client, admin_user, make_worker_client, mocker):
        worker, _client = reported(make_worker_client, 'mixed-log')
        info = mocker.patch.object(card_choice.logger, 'info')
        _post(admin_client, worker, _ticks((KEY, NVIDIA), (KEY_2, AMD)))
        line = info.call_args.args[0] % info.call_args.args[1:]
        assert line == ("Graphics card change for worker mixed-log refused (400): "
                        f"{MIXED_BRANDS_TEXT.format(brands='AMD, NVIDIA')} (user id={admin_user.pk})")

    def test_failed_post_keeps_the_pending_request(self, admin_client, make_worker_client):
        worker, _client = reported(make_worker_client, 'mixed-keep')
        first = _post(admin_client, worker, _ticks((KEY, NVIDIA))).data['card_choice_request']
        assert _post(admin_client, worker, _ticks((KEY, NVIDIA), (KEY_2, AMD))).status_code == 400
        assert row(worker).card_choice_request == first

    def test_other_pci_vendors_are_brands_too(self, admin_client, make_worker_client):
        worker, _client = reported(make_worker_client, 'mixed-pci', cards=[
            card(vendor_id=0x1AF4, backend=None), card(key=KEY_2, ticked=False, vendor_id=0x15AD, backend=None)])
        ids_a, ids_b = {'vendor_id': 0x1AF4, 'device_id': 0x2B85}, {'vendor_id': 0x15AD, 'device_id': 0x2B85}
        _assert_mixed(_post(admin_client, worker, _ticks((KEY, ids_a), (KEY_2, ids_b))), worker, 'PCI 15ad, PCI 1af4')


@pytest.mark.django_db
class TestOneBrandAccepted:

    @pytest.mark.parametrize('gpu_mode', ['split', 'combined'])
    def test_two_nvidia_cards(self, admin_client, make_worker_client, gpu_mode):
        worker, _client = reported(make_worker_client, f'one-{gpu_mode}', cards=[card(), SECOND_NVIDIA])
        resp = _post(admin_client, worker, _ticks((KEY, NVIDIA), (KEY_2, NVIDIA_2)), gpu_mode=gpu_mode)
        assert resp.status_code == 202, resp.content

    @pytest.mark.parametrize('pair', [(KEY, NVIDIA), (KEY_2, AMD)])
    def test_one_brand_on_a_machine_with_two(self, admin_client, make_worker_client, pair):
        """AC-6: a machine may have two brands; ticking the cards of either one works."""
        worker, _client = reported(make_worker_client, f'two-brand-host-{pair[0][-7:-5]}')
        assert _post(admin_client, worker, _ticks(pair)).status_code == 202


@pytest.mark.django_db
class TestUnknownBrand:
    """D7: no vendor id -> the Blender backend family; neither -> unknown (FR-PC-BR-2)."""

    def test_backend_family_names_the_brand_when_the_vendor_id_is_null(self, admin_client, make_worker_client):
        worker, _client = reported(make_worker_client, 'family-mixed', cards=[
            card(), card(key=KEY_2, ticked=False, backend='HIP', **NULL_IDS)])
        _assert_mixed(_post(admin_client, worker, _ticks((KEY, NVIDIA), (KEY_2, NULL_IDS))), worker, 'AMD, NVIDIA')

    def test_backend_family_of_the_same_brand_is_accepted(self, admin_client, make_worker_client):
        worker, _client = reported(make_worker_client, 'family-same', cards=[
            card(), card(key=KEY_2, ticked=False, backend='CUDA', **NULL_IDS)])
        assert _post(admin_client, worker, _ticks((KEY, NVIDIA), (KEY_2, NULL_IDS))).status_code == 202

    def test_two_unknown_brands_are_refused(self, admin_client, make_worker_client):
        worker, _client = reported(make_worker_client, 'unknown-two', cards=[
            card(backend=None, blender=False, **NULL_IDS),
            card(key=KEY_2, ticked=False, backend=None, blender=False, **NULL_IDS)])
        _assert_mixed(_post(admin_client, worker, _ticks((KEY, NULL_IDS), (KEY_2, NULL_IDS))), worker, 'unknown')

    def test_one_unknown_card_alone_is_accepted(self, admin_client, make_worker_client):
        worker, _client = reported(make_worker_client, 'unknown-one', cards=[
            card(backend=None, blender=False, **NULL_IDS), SECOND_NVIDIA])
        assert _post(admin_client, worker, _ticks((KEY, NULL_IDS))).status_code == 202

    @pytest.mark.parametrize('gpu_mode', ['split', 'combined'])
    def test_one_unknown_beside_one_known_brand_is_refused(self, admin_client, make_worker_client, gpu_mode):
        """D7: all ticked cards have one known brand; an unknown card goes only on its own."""
        worker, _client = reported(make_worker_client, f'unknown-known-{gpu_mode}', cards=[
            card(), card(key=KEY_2, ticked=False, backend=None, blender=False, **NULL_IDS)])
        resp = _post(admin_client, worker, _ticks((KEY, NVIDIA), (KEY_2, NULL_IDS)), gpu_mode=gpu_mode)
        _assert_mixed(resp, worker, 'NVIDIA, unknown')

    def test_two_unknown_and_one_known_name_both(self, admin_client, make_worker_client):
        third = 'pci:0000:02:00.0'
        worker, _client = reported(make_worker_client, 'unknown-three', cards=[
            card(), card(key=KEY_2, ticked=False, backend=None, **NULL_IDS),
            card(key=third, ticked=False, backend=None, **NULL_IDS)])
        resp = _post(admin_client, worker, _ticks((KEY, NVIDIA), (KEY_2, NULL_IDS), (third, NULL_IDS)),
                     shown=[KEY, KEY_2, third])
        _assert_mixed(resp, worker, 'NVIDIA, unknown')


@pytest.mark.django_db
class TestWorkerRefusalOutcome:
    """The worker's check 6b answers ``mixed_brands``; the manager stores it like any refusal."""

    def test_mixed_brands_outcome_is_accepted(self, admin_client, make_worker_client):
        worker, client = reported(make_worker_client, 'outcome-mixed')
        pending = _post(admin_client, worker, _ticks((KEY, NVIDIA))).data['card_choice_request']
        record = outcome(pending['id'], result='refused', code='mixed_brands', reason=MIXED_REASON, revision=3)
        resp = client.post(HB, {'hostname': 'outcome-mixed', 'cards': {**two_cards(), 'request_outcome': record}},
                           format='json')
        assert resp.status_code == 200, resp.data
        assert resp.data['card_choice_request'] is None
        assert (resp.data['card_choice_outcome']['code'], resp.data['card_choice_outcome']['reason']) == (
            'mixed_brands', MIXED_REASON)
        stored = row(worker)
        assert stored.card_choice_request == {}
        assert stored.card_choice_outcome['code'] == 'mixed_brands'

    def test_schema_lists_the_code(self, admin_client):
        schema = admin_client.get('/api/schema/', {'format': 'json'}).json()
        assert 'mixed_brands' in schema['components']['schemas']['WorkerCardOutcomeCodeEnum']['enum']
