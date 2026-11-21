# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``POST /api/heartbeat/{pk}/card-choice/`` (worker
card selection FR-CM-10, FR-CM-15, AC-23 manager part, AC-25..29, AC-32,
AC-34, AC-35, AC-40): the checks in their order, 202 and replace, the
``requested_by`` form, ``{"detail"}`` bodies, and the log lines.
"""

import json

import pytest
from rest_framework.test import APIClient

from tests.unit.manager.card_report_helpers import KEY, KEY_2, alert, card
from workers.services import card_brand, card_choice, card_choice_rules as rules

from .card_choice_helpers import AMD, NVIDIA, NVIDIA_2, SECOND_CARD, SECOND_NVIDIA, body, reported, row, url
from .token_admin_helpers import session_client

TWO = [{'key': KEY, **NVIDIA}, {'key': KEY_2, **AMD}]
# Two cards of one brand (Unreal render per card FR-PC-BR-5); the report needs SECOND_NVIDIA.
TWO_NVIDIA = [{'key': KEY, **NVIDIA}, {'key': KEY_2, **NVIDIA_2}]


def _nothing_stored(worker):
    stored = row(worker)
    return stored.card_choice_request == {} and stored.card_choice_issued == []


def _refused(resp, code, detail=None):
    assert resp.status_code == code, resp.content
    assert set(resp.json()) == {'detail'}
    if detail is not None:
        assert resp.json()['detail'] == detail


@pytest.fixture
def worker(make_worker_client):
    return reported(make_worker_client, 'choice-host')[0]


@pytest.mark.django_db
class TestAccepted:

    def test_admin_sends_a_change(self, admin_client, admin_user, worker, mocker):
        info = mocker.patch.object(card_choice.logger, 'info')
        resp = admin_client.post(url(worker.pk), body(), format='json')
        assert resp.status_code == 202, resp.data
        pending = resp.data['card_choice_request']
        assert resp.data['replaced'] is None
        assert set(pending) == {'method', 'gpu_mode', 'cards', 'shown', 'base_revision',
                                'id', 'requested_by', 'requested_at'}
        assert {k: pending[k] for k in body()} == body()
        assert pending['requested_by'] == f'user id={admin_user.pk}'
        assert len(pending['id']) == 32 and int(pending['id'], 16) >= 0
        assert pending['requested_at'].endswith('+00:00')
        stored = row(worker)
        assert stored.card_choice_request == pending
        assert stored.card_choice_issued == [{k: pending[k] for k in ('id', 'requested_by', 'requested_at')}]
        line = info.call_args.args[0] % info.call_args.args[1:]
        assert line == f"Graphics card change {pending['id']} for worker choice-host requested by user id={admin_user.pk}"
        assert 'admin' not in json.dumps(resp.data)

    def test_second_post_replaces_and_names_the_old_id(self, admin_client, worker):
        """AC-29: the old id stays issued, so its late outcome is still accepted."""
        first = admin_client.post(url(worker.pk), body(), format='json').data['card_choice_request']
        resp = admin_client.post(url(worker.pk), body(method='cpu_gpu'), format='json')
        assert resp.status_code == 202
        assert resp.data['replaced'] == first['id']
        stored = row(worker)
        assert stored.card_choice_request['id'] == resp.data['card_choice_request']['id']
        assert [entry['id'] for entry in stored.card_choice_issued] == [first['id'], stored.card_choice_request['id']]

    def test_issued_list_keeps_the_last_eight(self, admin_client, worker):
        ids = [admin_client.post(url(worker.pk), body(), format='json').data['card_choice_request']['id']
               for _ in range(10)]
        assert [entry['id'] for entry in row(worker).card_choice_issued] == ids[-8:]

    @pytest.mark.parametrize('overrides', [
        {'method': 'cpu', 'cards': [], 'shown': []},
        {'method': 'cpu', 'cards': [{'key': KEY, **NVIDIA}]},
        {'cards': [{'key': KEY, **NVIDIA}], 'shown': [KEY]},
    ])
    def test_other_valid_bodies(self, admin_client, worker, overrides):
        assert admin_client.post(url(worker.pk), body(**overrides), format='json').status_code == 202

    def test_combined_of_one_family(self, admin_client, make_worker_client):
        worker, _client = reported(make_worker_client, 'combined', cards=[
            card(backend='CUDA'), card(key=KEY_2, ticked=False, backend='OPTIX', **NVIDIA_2)])
        resp = admin_client.post(url(worker.pk), body(gpu_mode='combined', cards=TWO_NVIDIA), format='json')
        assert resp.status_code == 202

    def test_keeping_a_held_ticked_card_is_accepted(self, admin_client, make_worker_client):
        """AC-26: only a new tick of a held card is refused."""
        worker, _client = reported(make_worker_client, 'held-kept', cards=[card(state='held'), SECOND_CARD],
                                   alerts=[alert(kind='card_changed')])
        assert admin_client.post(url(worker.pk), body(), format='json').status_code == 202

    def test_staff_session_through_real_login(self, worker):
        client = session_client('staff-choice', 'pw-12345678', is_staff=True)
        assert client.post(url(worker.pk), body(), format='json').status_code == 202


@pytest.mark.django_db
class TestPermission:
    """AC-32: admin only, for POST and DELETE."""

    @pytest.mark.parametrize('method', ['post', 'delete'])
    def test_worker_token_gets_403(self, make_worker_client, method):
        worker, client = reported(make_worker_client, 'own-token')
        _refused(getattr(client, method)(url(worker.pk), body(), format='json'), 403)
        assert _nothing_stored(worker)

    @pytest.mark.parametrize('method', ['post', 'delete'])
    def test_non_staff_user_gets_403(self, worker, method):
        client = session_client('plain-choice', 'pw-12345678', is_staff=False)
        _refused(getattr(client, method)(url(worker.pk), body(), format='json'), 403)

    @pytest.mark.parametrize('method', ['post', 'delete'])
    def test_anonymous_gets_401(self, worker, method):
        _refused(getattr(APIClient(), method)(url(worker.pk), body(), format='json'), 401)
        assert _nothing_stored(worker)

    @pytest.mark.parametrize('method', ['post', 'delete'])
    def test_session_without_csrf_token_gets_403(self, worker, method):
        client = session_client('csrf-staff', 'pw-12345678', is_staff=True, enforce_csrf=True)
        resp = getattr(client, method)(url(worker.pk), body(), format='json')
        _refused(resp, 403)
        assert 'CSRF' in resp.json()['detail']
        assert _nothing_stored(worker)

    def test_get_is_not_allowed(self, admin_client, worker):
        _refused(admin_client.get(url(worker.pk)), 405)


@pytest.mark.django_db
class TestLengthAndMediaType:
    """AC-34: the length is checked before the body is read."""

    def test_missing_length_is_411(self, admin_client, worker, mocker):
        read = mocker.spy(card_choice, '_body')
        _refused(admin_client.generic('POST', url(worker.pk)), 411, card_choice.LENGTH_REQUIRED)
        _refused(admin_client.generic('POST', url(worker.pk), CONTENT_LENGTH=''), 411)
        assert read.call_count == 0
        assert _nothing_stored(worker)

    def test_too_large_is_413(self, admin_client, worker, mocker):
        read = mocker.spy(card_choice, '_body')
        big = json.dumps({**body(), 'pad': 'x' * 8200})
        _refused(admin_client.generic('POST', url(worker.pk), big, content_type='application/json'),
                 413, card_choice.TOO_LARGE)
        assert read.call_count == 0
        assert _nothing_stored(worker)

    @pytest.mark.parametrize('length', ['abc', '-1', '8193', '1' * 13])
    def test_bad_length_is_413(self, admin_client, worker, length):
        resp = admin_client.generic('POST', url(worker.pk), json.dumps(body()), content_type='application/json',
                                    CONTENT_LENGTH=length)
        _refused(resp, 413)

    def test_body_of_exactly_the_limit_is_read(self, admin_client, worker):
        text = json.dumps(body())
        text = text[:-1] + ' ' * (card_choice.MAX_BODY_BYTES - len(text)) + '}'
        assert len(text) == card_choice.MAX_BODY_BYTES
        resp = admin_client.generic('POST', url(worker.pk), text, content_type='application/json')
        assert resp.status_code == 202

    def test_form_body_is_415(self, admin_client, worker):
        resp = admin_client.post(url(worker.pk), 'method=gpu&gpu_mode=split', content_type='application/x-www-form-urlencoded')
        _refused(resp, 415)
        assert _nothing_stored(worker)

    def test_invalid_json_is_400(self, admin_client, worker, mocker):
        info = mocker.patch.object(card_choice.logger, 'info')
        resp = admin_client.generic('POST', url(worker.pk), '{"method": ', content_type='application/json')
        _refused(resp, 400, card_choice.BAD_JSON)
        assert info.call_count == 1

    @pytest.mark.parametrize('text', ['[' * 8000, '[' * 4000 + ']' * 4000])
    def test_deeply_nested_json_is_400_not_500(self, admin_client, worker, text, mocker):
        """Whether the parser recurses too far depends on the thread's stack, so it is forced here."""
        mocker.patch('rest_framework.parsers.JSONParser.parse', side_effect=RecursionError)
        assert len(text) <= card_choice.MAX_BODY_BYTES
        resp = admin_client.generic('POST', url(worker.pk), text, content_type='application/json')
        _refused(resp, 400, card_choice.BAD_JSON)
        assert _nothing_stored(worker)

    def test_deeply_nested_json_without_a_forced_error_is_400(self, admin_client, worker):
        resp = admin_client.generic('POST', url(worker.pk), '[' * 4000 + ']' * 4000, content_type='application/json')
        _refused(resp, 400)

    @pytest.mark.parametrize('pk', ['999999', 'abc', '1' * 20])
    def test_unknown_worker_is_404(self, admin_client, worker, pk):
        _refused(admin_client.post(url(pk), body(), format='json'), 404, 'Worker not found.')

    @pytest.mark.parametrize('pk', ['999999', 'abc'])
    def test_length_is_checked_before_the_worker(self, admin_client, worker, pk):
        _refused(admin_client.generic('POST', url(pk)), 411)
        _refused(admin_client.delete(url(pk)), 404, 'Worker not found.')

    def test_unknown_worker_is_404_before_the_body_is_read(self, admin_client, worker):
        _refused(admin_client.post(url(worker.pk + 1000), {'garbage': 1}, format='json'), 404)


@pytest.mark.django_db
class TestOrder:

    def test_shape_400_comes_before_the_state_409(self, admin_client, make_worker_client):
        """AC-34: ``base_revision: true`` to a worker under env override is 400, not 409."""
        worker, _client = reported(make_worker_client, 'env-shape', env_override=True)
        _refused(admin_client.post(url(worker.pk), body(base_revision=True), format='json'), 400, rules.BAD_REVISION)
        _refused(admin_client.post(url(worker.pk), body(), format='json'), 409, rules.ENV_OVERRIDE)

    @pytest.mark.parametrize('status_overrides, detail', [
        ({'schema': 2}, rules.NO_REPORT),
        ({'env_override': True, 'manager_changes_allowed': False}, rules.ENV_OVERRIDE),
        ({'manager_changes_allowed': False}, rules.NOT_ALLOWED),
        ({'revision': 4}, rules.STALE),
    ])
    def test_state_409s(self, admin_client, make_worker_client, status_overrides, detail, mocker):
        """AC-25, AC-27, AC-28."""
        worker, _client = reported(make_worker_client, 'state', **status_overrides)
        info = mocker.patch.object(card_choice.logger, 'info')
        _refused(admin_client.post(url(worker.pk), body(), format='json'), 409, detail)
        assert _nothing_stored(worker)
        line = info.call_args.args[0] % info.call_args.args[1:]
        assert line.startswith('Graphics card change for worker state refused (409): ')
        assert detail in line and line.endswith(')') and 'user id=' in line

    def test_worker_that_never_reported_is_409(self, admin_client, make_worker_client):
        worker, _client, _token = make_worker_client('silent')
        _refused(admin_client.post(url(worker.pk), body(), format='json'), 409, rules.NO_REPORT)

    def test_state_409_comes_before_the_report_400(self, admin_client, make_worker_client):
        worker, _client = reported(make_worker_client, 'stale-unknown', revision=9)
        resp = admin_client.post(url(worker.pk), body(shown=[KEY, 'pci:0000:99:00.0']), format='json')
        _refused(resp, 409, rules.STALE)

    @pytest.mark.parametrize('overrides, detail', [
        ({'shown': [KEY, 'pci:0000:99:00.0']}, rules.NOT_REPORTED.format(key='pci:0000:99:00.0')),
        ({'cards': [{'key': KEY, 'vendor_id': 0x10DE, 'device_id': 1}]}, rules.IDS_DIFFER.format(key=KEY)),
        # The brand check (FR-PC-BR-5) comes before the one-family rule.
        ({'gpu_mode': 'combined', 'cards': TWO}, card_brand.MIXED_BRANDS_TEXT.format(brands='AMD, NVIDIA')),
    ])
    def test_report_400s(self, admin_client, worker, overrides, detail):
        _refused(admin_client.post(url(worker.pk), body(**overrides), format='json'), 400, detail)
        assert _nothing_stored(worker)

    def test_combined_with_a_null_backend_is_400(self, admin_client, make_worker_client):
        """AC-34: one CUDA card and one card with a null backend."""
        worker, _client = reported(make_worker_client, 'null-backend', cards=[
            card(backend='CUDA'), card(key=KEY_2, ticked=False, backend=None, blender=False, **NVIDIA_2)])
        resp = admin_client.post(url(worker.pk), body(gpu_mode='combined', cards=TWO_NVIDIA), format='json')
        _refused(resp, 400, rules.ONE_FAMILY)

    @pytest.mark.parametrize('kind', ['card_lost', 'card_changed', 'card_lost_blender'])
    def test_newly_ticking_a_held_card_is_400(self, admin_client, make_worker_client, kind):
        """AC-26."""
        worker, _client = reported(make_worker_client, f'held-{kind}', cards=[card(), SECOND_NVIDIA],
                                   alerts=[alert(key=KEY_2, kind=kind)])
        resp = admin_client.post(url(worker.pk), body(cards=TWO_NVIDIA), format='json')
        _refused(resp, 400, rules.HELD.format(key=KEY_2))

    def test_failed_post_keeps_the_pending_request(self, admin_client, worker):
        first = admin_client.post(url(worker.pk), body(), format='json').data['card_choice_request']
        _refused(admin_client.post(url(worker.pk), body(base_revision=2), format='json'), 409)
        assert row(worker).card_choice_request == first
