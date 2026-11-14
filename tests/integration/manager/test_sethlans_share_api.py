# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: ``GET``/``PUT /api/sethlans-share/`` (Unreal foundation
FR-MS-1, FR-MS-2, FR-MS-6, FR-T-3, AC-32).
"""

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from workers.models import Job, ManagerSettings
from workers.views import sethlans_share as share_view

from .unreal_helpers import BUILD, RENDER_SETTINGS, caps, set_share, unreal_asset

URL = '/api/sethlans-share/'
CANONICAL = r'\\alpha.lan\mestrella\applications\unreal'


@pytest.fixture
def user_client(db):
    user = get_user_model().objects.create_user(username='artist', password='x')
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _stored():
    return ManagerSettings.objects.values_list('sethlans_share', flat=True).get(pk=1)


@pytest.mark.django_db
class TestPermissions:

    def test_put_requires_an_admin(self, user_client, worker_with_token):
        _worker, worker_client = worker_with_token
        for client in (user_client, worker_client, APIClient()):
            resp = client.put(URL, {'sethlans_share': CANONICAL}, format='json')
            assert resp.status_code == 403
        assert _stored() == ''

    def test_put_refuses_a_staff_worker_account(self, worker_with_token):
        worker, client = worker_with_token
        worker.user.is_staff = True
        worker.user.save(update_fields=['is_staff'])
        resp = client.put(URL, {'sethlans_share': CANONICAL}, format='json')
        assert resp.status_code == 403
        assert _stored() == ''

    def test_get_for_admin_and_users_not_workers(self, admin_client, user_client, worker_with_token):
        set_share(CANONICAL)
        _worker, worker_client = worker_with_token
        for client in (admin_client, user_client):
            resp = client.get(URL)
            assert resp.status_code == 200
            assert resp.data == {'sethlans_share': CANONICAL}
        assert worker_client.get(URL).status_code == 403
        assert APIClient().get(URL).status_code == 403


@pytest.mark.django_db
class TestPut:

    @pytest.mark.parametrize('value, stored', [
        (r'\\ALPHA.LAN\mestrella\applications\unreal', CANONICAL),
        (CANONICAL + '\\', CANONICAL),
        (r'\\nas.lan\sethlans', r'\\nas.lan\sethlans'),
    ])
    def test_stores_the_canonical_form(self, admin_client, value, stored):
        resp = admin_client.put(URL, {'sethlans_share': value}, format='json')
        assert resp.status_code == 200, resp.data
        assert resp.data == {'sethlans_share': stored}
        assert _stored() == stored

    @pytest.mark.parametrize('value', [
        'C:\\share', '//nas/share', r'\\nas', r'\\?\C:\x', r'\\nas\c$', r'\\nas\IPC$',
        r'\\nas\share\..\x', r'\\nas\share\\x', r'\\nas\share\a:b', r'\\nas\share\x.',
        r'\\nas\share\ x', r'\\nas\share\PROGRA~1', '\\\\nas\\share\\a\u202eb',
        '\\\\nas\\share\\a\u200bb', '\\\\nas\\share\\e\u0301', '\\\\nas\\share\\a\x01b',
        CANONICAL + ' ', r'\\-bad\share', 'x' * 1025,
    ])
    def test_rejects_every_rule(self, admin_client, value):
        resp = admin_client.put(URL, {'sethlans_share': value}, format='json')
        assert resp.status_code == 400
        assert list(resp.data) == ['sethlans_share']
        assert _stored() == ''

    def test_empty_clears(self, admin_client):
        set_share(CANONICAL)
        resp = admin_client.put(URL, {'sethlans_share': ''}, format='json')
        assert (resp.status_code, resp.data) == (200, {'sethlans_share': ''})
        assert _stored() == ''

    def test_single_label_host_warns_but_stores(self, admin_client):
        resp = admin_client.put(URL, {'sethlans_share': r'\\nas\sethlans'}, format='json')
        assert resp.status_code == 200
        assert resp.data['warnings'] == [share_view.SINGLE_LABEL_WARNING]
        assert _stored() == r'\\nas\sethlans'

    def test_dotted_host_has_no_warnings(self, admin_client):
        resp = admin_client.put(URL, {'sethlans_share': CANONICAL}, format='json')
        assert 'warnings' not in resp.data

    def test_audit_line(self, admin_client):
        set_share(r'\\old.lan\share')
        with patch.object(share_view, 'logger') as log:
            admin_client.put(URL, {'sethlans_share': CANONICAL}, format='json')
        log.warning.assert_called_once()
        args = log.warning.call_args.args
        assert args[0] % args[1:] == (
            f"Sethlans share changed by admin from 127.0.0.1: "
            f"'\\\\old.lan\\share' -> '{CANONICAL}'"
        )

    def test_missing_settings_row_is_503_not_500(self, admin_client):
        ManagerSettings.objects.all().delete()
        resp = admin_client.put(URL, {'sethlans_share': CANONICAL}, format='json')
        assert resp.status_code == 503
        assert admin_client.get(URL).data == {'sethlans_share': ''}

    def test_username_is_escaped_in_the_audit_line(self, db):
        user = get_user_model().objects.create_superuser(username='ad\nmin', password='x')
        client = APIClient()
        client.force_authenticate(user=user)
        with patch.object(share_view, 'logger') as log:
            client.put(URL, {'sethlans_share': CANONICAL}, format='json')
        args = log.warning.call_args.args
        assert '\n' not in args[1]
        assert args[1] == r'ad\nmin'

    def test_enrollment_key_timestamp_is_unchanged(self, admin_client):
        before = ManagerSettings.objects.get(pk=1).enrollment_key_updated_at
        admin_client.put(URL, {'sethlans_share': CANONICAL}, format='json')
        assert ManagerSettings.objects.get(pk=1).enrollment_key_updated_at == before


@pytest.mark.django_db
class TestNonCanonicalStoredValue:
    """A value written around the view is treated as not configured."""

    def test_get_claim_and_routing_note(self, admin_client, worker_with_token):
        set_share(r'\\ALPHA.LAN\share')
        worker, client = worker_with_token
        worker.unreal_capabilities = caps(share=r'\\alpha.lan\share')
        worker.save(update_fields=['unreal_capabilities'])
        job = Job.objects.create(
            name='ShareJob', asset=unreal_asset(), output_file_pattern='//r/f.png',
            render_settings=RENDER_SETTINGS, render_device='GPU',
        )
        assert admin_client.get(URL).data == {'sethlans_share': ''}
        note = admin_client.get(f'/api/jobs/{job.pk}/').data['routing_note']
        assert note == "The Sethlans share is not configured."
        resp = client.post(f'/api/jobs/{job.pk}/claim/', {'worker_id': worker.pk}, format='json')
        assert (resp.status_code, resp.data) == (409, {'error': "The Sethlans share is not configured."})
        assert job.engine_build == BUILD


@pytest.mark.django_db
class TestHeartbeatCarriesNoShare:

    def test_registration_and_heartbeat(self, make_worker_client):
        set_share(CANONICAL)
        _worker, client, _token = make_worker_client('share-hb')
        reg = client.post('/api/heartbeat/', {'hostname': 'share-hb', 'os': 'Windows'}, format='json')
        beat = client.post('/api/heartbeat/', {'hostname': 'share-hb'}, format='json')
        for resp in (reg, beat):
            assert resp.status_code == 200, resp.data
            assert 'sethlans_share' not in resp.data
