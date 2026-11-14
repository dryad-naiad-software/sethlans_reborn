# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: ``unreal_capabilities`` in registration and heartbeats
(Unreal foundation FR-RT-7, FR-RT-8, FR-T-3).
"""

import pytest

from workers.models import Worker

from .unreal_helpers import BUILD, SHARE, caps

HB = '/api/heartbeat/'


def _post(client, hostname, **extra):
    resp = client.post(HB, {'hostname': hostname, **extra}, format='json')
    assert resp.status_code == 200, resp.data
    return resp


@pytest.mark.django_db
class TestCapabilities:

    def test_registration_stores_and_returns_the_sanitized_value(self, make_worker_client):
        worker, client, _token = make_worker_client('caps-reg')
        sent = {**caps(), 'evil': '<x>', 'disabled_reason': 'bad`<b>`'}
        resp = _post(client, 'caps-reg', os='Windows', unreal_capabilities=sent)
        expected = {**caps(), 'disabled_reason': 'bad??b??'}
        assert resp.data['unreal_capabilities'] == expected
        assert Worker.objects.get(pk=worker.pk).unreal_capabilities == expected

    def test_registration_without_the_key_stores_empty(self, make_worker_client):
        worker, client, _token = make_worker_client('caps-empty')
        Worker.objects.filter(pk=worker.pk).update(unreal_capabilities=caps())
        _post(client, 'caps-empty', os='Windows')
        assert Worker.objects.get(pk=worker.pk).unreal_capabilities == {}

    def test_heartbeat_updates_only_when_present(self, make_worker_client):
        worker, client, _token = make_worker_client('caps-hb')
        Worker.objects.filter(pk=worker.pk).update(unreal_capabilities=caps())
        _post(client, 'caps-hb')
        assert Worker.objects.get(pk=worker.pk).unreal_capabilities == caps()
        _post(client, 'caps-hb', unreal_capabilities=caps(enabled=False, builds=()))
        assert Worker.objects.get(pk=worker.pk).unreal_capabilities == caps(enabled=False, builds=())

    def test_invalid_share_and_reachability_are_normalized(self, make_worker_client):
        worker, client, _token = make_worker_client('caps-share')
        _post(client, 'caps-share', unreal_capabilities=caps(share=SHARE.upper(), reachable='yes'))
        stored = Worker.objects.get(pk=worker.pk).unreal_capabilities
        assert (stored['share'], stored['share_reachable']) == ('', False)

    def test_bad_installs_are_dropped(self, make_worker_client):
        worker, client, _token = make_worker_client('caps-installs')
        sent = caps()
        sent['installs'].append({'build': 'x', 'supported': True, 'source': 'launcher'})
        _post(client, 'caps-installs', unreal_capabilities=sent)
        installs = Worker.objects.get(pk=worker.pk).unreal_capabilities['installs']
        assert [i['build'] for i in installs] == [BUILD]

    def test_garbage_is_stored_empty(self, make_worker_client):
        worker, client, _token = make_worker_client('caps-garbage')
        _post(client, 'caps-garbage', unreal_capabilities='yes please')
        assert Worker.objects.get(pk=worker.pk).unreal_capabilities == {}

    def test_admin_listing_exposes_it_but_not_boot_id(self, admin_client, make_worker_client):
        worker, _client, _token = make_worker_client('caps-list')
        Worker.objects.filter(pk=worker.pk).update(unreal_capabilities=caps(), boot_id='c' * 32)
        row = next(w for w in admin_client.get(HB).data if w['hostname'] == 'caps-list')
        assert row['unreal_capabilities'] == caps()
        assert 'boot_id' not in row
