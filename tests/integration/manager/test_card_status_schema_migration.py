# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for the card fields' migration, serializer and OpenAPI
shapes (worker card selection FR-CR-2, FR-CR-4, FR-CR-5, FR-CR-7).
"""

import json
from io import StringIO

import pytest
from django.contrib import admin
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, RequestFactory

from workers.models import Worker
from workers.serializers import WorkerSerializer

BEFORE = [('workers', '0023_yield_reason_worker_stops')]
AFTER = [('workers', '0024_worker_card_status')]
CARD_COLUMNS = {'card_status', 'card_alert_dismissals'}


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


def _columns():
    with connection.cursor() as cursor:
        return {c.name for c in connection.introspection.get_table_description(cursor, 'workers_worker')}


@pytest.fixture
def at_0023(transactional_db):
    try:
        yield _migrate(BEFORE)
    finally:
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db(transaction=True)
class TestMigration:

    def test_existing_rows_get_empty_values_and_reverse_drops_the_columns(self, at_0023):
        at_0023.get_model('workers', 'Worker').objects.create(hostname='old-row')
        apps = _migrate(AFTER)
        row = apps.get_model('workers', 'Worker').objects.get(hostname='old-row')
        assert (row.card_status, row.card_alert_dismissals) == ({}, [])
        _migrate(BEFORE)
        assert not CARD_COLUMNS & _columns()

    def test_no_model_change_lacks_a_migration(self):
        out = StringIO()
        call_command('makemigrations', 'workers', '--check', '--dry-run', stdout=out)


@pytest.mark.django_db
class TestSerializer:

    def test_help_texts(self):
        assert 'not verified' in Worker._meta.get_field('card_status').help_text
        assert 'dismiss action' in Worker._meta.get_field('card_alert_dismissals').help_text

    @pytest.mark.parametrize('value', [{}, None])
    def test_empty_card_status_is_null(self, value):
        worker = Worker(hostname='s', card_status=value)
        assert WorkerSerializer(worker).data['card_status'] is None

    def test_fields_are_read_only(self):
        fields = WorkerSerializer().fields
        assert fields['card_status'].read_only and fields['card_alert_dismissals'].read_only

    def test_django_admin_cannot_edit_the_card_fields(self, admin_user):
        """The admin form would save them without the row lock and unvalidated (D24).

        Read-only fields are excluded from the admin's model form.  The form
        itself is not built here: Django 5.2 warns about the ``ui_url``
        URLField's default scheme whenever the Worker admin form is built.
        """
        model_admin = admin.site._registry[Worker]
        request = RequestFactory().get('/admin/')
        request.user = admin_user
        worker = Worker.objects.create(hostname='admin-form')
        assert CARD_COLUMNS <= set(model_admin.get_readonly_fields(request, worker))
        assert CARD_COLUMNS <= set(model_admin.get_readonly_fields(request))


@pytest.mark.django_db
class TestOpenAPI:

    @pytest.fixture
    def schema(self):
        resp = Client().get('/api/schema/?format=json', HTTP_ACCEPT='application/json')
        assert resp.status_code == 200
        return json.loads(resp.content.decode('utf-8'))

    def test_card_status_is_nullable_with_optional_inner_fields(self, schema):
        comps = schema['components']['schemas']
        prop = comps['WorkerHeartbeatResponse']['properties']['card_status']
        assert prop['nullable'] is True and prop['readOnly'] is True
        for name in ('WorkerCardStatus', 'WorkerCard', 'WorkerCardAlert', 'WorkerCardNotice', 'WorkerCardWarning'):
            assert 'required' not in comps[name], name
        assert 'unknown' in comps['WorkerCardStateEnum']['enum']
        assert comps['WorkerCardAlert']['properties']['raised_at']['nullable'] is True

    def test_card_enum_names_are_pinned(self, schema):
        comps = schema['components']['schemas']

        def ref(component, prop):
            node = comps[component]['properties'][prop]
            node = node.get('allOf', [node])[0] if '$ref' not in node else node
            return node['$ref'].rsplit('/', 1)[1]
        assert ref('WorkerCardStatus', 'method') == 'WorkerCardMethodEnum'
        assert ref('WorkerCardStatus', 'gpu_mode') == 'WorkerCardGpuModeEnum'
        assert ref('WorkerCard', 'state') == 'WorkerCardStateEnum'
        assert ref('WorkerCardAlert', 'kind') == 'WorkerCardAlertKindEnum'
        assert ref('WorkerCardNotice', 'kind') == 'WorkerCardNoticeKindEnum'
        backend = comps['WorkerCard']['properties']['backend']
        assert {'$ref': '#/components/schemas/WorkerCardBackendEnum'} in backend.get('oneOf', [backend])

    def test_heartbeat_answer_documents_the_dismissals(self, schema):
        props = schema['components']['schemas']['WorkerHeartbeatResponse']['properties']
        assert props['dismissed_card_alerts']['type'] == 'array'
        assert props['card_alert_dismissals']['readOnly'] is True

    def test_dismiss_action_is_documented(self, schema):
        post = schema['paths']['/api/heartbeat/{id}/dismiss-card-alert/']['post']
        assert set(post['responses']) == {'200', '400', '401', '403', '404'}
        body = post['requestBody']['content']['application/json']['schema']['$ref']
        assert body.endswith('/DismissCardAlertRequest')
