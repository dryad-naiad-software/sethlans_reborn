# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the card-choice fields' migration, serializer and
OpenAPI shapes (worker card selection FR-CM-9, FR-CM-14, FR-CR-4, D3,
AC-35).
"""

import json
from types import SimpleNamespace

import pytest
from django.contrib import admin
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, RequestFactory
from django.test.utils import CaptureQueriesContext

from workers.models import Worker
from workers.serializers import WorkerSerializer
from workers.views.heartbeat_schema import WorkerHeartbeatResponseSerializer

BEFORE = [('workers', '0024_worker_card_status')]
AFTER = [('workers', '0025_worker_card_choice')]
CHOICE_COLUMNS = {'card_choice_request', 'card_choice_issued', 'card_choice_outcome'}


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


def _columns():
    with connection.cursor() as cursor:
        return {c.name for c in connection.introspection.get_table_description(cursor, 'workers_worker')}


@pytest.fixture
def at_0024(transactional_db):
    try:
        yield _migrate(BEFORE)
    finally:
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db(transaction=True)
class TestMigration:

    def test_existing_rows_get_empty_values_and_reverse_drops_the_columns(self, at_0024):
        at_0024.get_model('workers', 'Worker').objects.create(hostname='old-row', card_status={'schema': 1})
        apps = _migrate(AFTER)
        found = apps.get_model('workers', 'Worker').objects.get(hostname='old-row')
        assert (found.card_choice_request, found.card_choice_issued, found.card_choice_outcome) == ({}, [], {})
        assert found.card_status == {'schema': 1}
        _migrate(BEFORE)
        assert not CHOICE_COLUMNS & _columns()


@pytest.mark.django_db
class TestSerializer:

    def test_help_texts(self):
        def text(name):
            return Worker._meta.get_field(name).help_text
        assert 'card-choice action' in text('card_choice_request')
        assert 'card-choice action' in text('card_choice_issued')
        assert 'Not exposed' in text('card_choice_issued')
        assert 'not verified' in text('card_choice_outcome')

    @pytest.mark.parametrize('value', [{}, None])
    def test_empty_values_are_null(self, value):
        data = WorkerSerializer(Worker(hostname='s', card_status=value, card_choice_request=value,
                                       card_choice_outcome=value)).data
        assert (data['card_status'], data['card_choice_request'], data['card_choice_outcome']) == (None, None, None)

    def test_values_are_shown_and_read_only_and_the_issued_list_is_not(self):
        worker = Worker(hostname='s', card_choice_request={'id': 'a' * 32}, card_choice_outcome={'id': 'b' * 32},
                        card_choice_issued=[{'id': 'a' * 32}])
        data = WorkerSerializer(worker).data
        assert data['card_choice_request'] == {'id': 'a' * 32}
        assert data['card_choice_outcome'] == {'id': 'b' * 32}
        assert 'card_choice_issued' not in data
        fields = WorkerSerializer().fields
        assert fields['card_choice_request'].read_only and fields['card_choice_outcome'].read_only
        assert 'card_choice_issued' not in WorkerHeartbeatResponseSerializer().fields

    def test_django_admin_cannot_edit_the_choice_fields(self, admin_user):
        """Written only under the row lock (D24); see the chunk 5 test for why no form is built."""
        model_admin = admin.site._registry[Worker]
        request = RequestFactory().get('/admin/')
        request.user = admin_user
        assert CHOICE_COLUMNS <= set(model_admin.get_readonly_fields(request, Worker.objects.create(hostname='a')))


@pytest.mark.django_db
class TestAdminSave:
    """The admin writes only the edited fields, never the card fields (D24)."""

    def _save(self, admin_user, obj, changed, change=True):
        request = RequestFactory().post('/admin/')
        request.user = admin_user
        admin.site._registry[Worker].save_model(request, obj, SimpleNamespace(changed_data=changed), change)

    def test_edit_keeps_card_fields_written_meanwhile(self, admin_user):
        stale = Worker.objects.create(hostname='admin-edit', card_choice_request={'id': 'a' * 32},
                                      card_choice_issued=[{'id': 'a' * 32}], card_alert_dismissals=['b' * 32])
        Worker.objects.filter(pk=stale.pk).update(
            card_choice_request={}, card_choice_issued=[{'id': 'a' * 32}, {'id': 'c' * 32}],
            card_alert_dismissals=[])
        stale.is_active = False
        self._save(admin_user, stale, ['is_active'])
        fresh = Worker.objects.get(pk=stale.pk)
        assert fresh.is_active is False
        assert fresh.card_choice_request == {}
        assert [e['id'] for e in fresh.card_choice_issued] == ['a' * 32, 'c' * 32]
        assert fresh.card_alert_dismissals == []

    def test_edit_without_changes_writes_nothing(self, admin_user):
        stale = Worker.objects.create(hostname='admin-none', card_choice_request={'id': 'a' * 32})
        Worker.objects.filter(pk=stale.pk).update(card_choice_request={})
        with CaptureQueriesContext(connection) as ctx:
            self._save(admin_user, stale, [])
        assert not [q for q in ctx.captured_queries if q['sql'].startswith('UPDATE')]
        assert Worker.objects.get(pk=stale.pk).card_choice_request == {}

    def test_add_saves_the_whole_row(self, admin_user):
        self._save(admin_user, Worker(hostname='admin-add'), ['hostname'], change=False)
        assert Worker.objects.filter(hostname='admin-add').exists()


@pytest.mark.django_db
class TestOpenAPI:

    @pytest.fixture
    def schema(self):
        resp = Client().get('/api/schema/?format=json', HTTP_ACCEPT='application/json')
        assert resp.status_code == 200
        return json.loads(resp.content.decode('utf-8'))

    def test_worker_fields_are_nullable_with_optional_inner_fields(self, schema):
        comps = schema['components']['schemas']
        props = comps['WorkerHeartbeatResponse']['properties']
        assert 'card_choice_issued' not in props
        for name in ('card_choice_request', 'card_choice_outcome'):
            assert props[name]['nullable'] is True and props[name]['readOnly'] is True, name
        for name in ('WorkerCardChoiceRequest', 'WorkerCardChoiceOutcome', 'WorkerCardChoiceCard'):
            assert 'required' not in comps[name], name
        assert 'FR-CM-12' in comps['WorkerHeartbeatResponse']['properties']['card_choice_request']['description']

    def test_outcome_enums_are_pinned(self, schema):
        comps = schema['components']['schemas']
        assert comps['WorkerCardOutcomeResultEnum']['enum'] == ['applied', 'refused']
        assert 'stale_revision' in comps['WorkerCardOutcomeCodeEnum']['enum']

    def test_card_choice_action_is_documented(self, schema):
        path = schema['paths']['/api/heartbeat/{id}/card-choice/']
        assert set(path['post']['responses']) == {'202', '400', '401', '403', '404', '409', '411', '413', '415'}
        assert set(path['delete']['responses']) == {'204', '401', '403', '404', '409'}
        assert 'requestBody' not in path['delete']
        content = path['post']['requestBody']['content']
        assert set(content) == {'application/json'}
        assert content['application/json']['schema']['$ref'].endswith('/CardChoiceBody')
        comps = schema['components']['schemas']
        assert set(comps['CardChoiceBody']['required']) == {'method', 'gpu_mode', 'cards', 'shown', 'base_revision'}
        sent = path['post']['responses']['202']['content']['application/json']['schema']['$ref']
        assert sent.endswith('/CardChoiceSendResponse')
        request = comps['CardChoiceSendResponse']['properties']['card_choice_request']
        assert request == {'$ref': '#/components/schemas/CardChoiceSentRequest'}
        assert set(comps['CardChoiceSentRequest']['required']) == {
            'method', 'gpu_mode', 'cards', 'shown', 'base_revision', 'id', 'requested_by', 'requested_at'}
        assert comps['CardChoiceSendResponse']['properties']['replaced']['nullable'] is True
