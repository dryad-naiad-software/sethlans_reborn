# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the PCI ``revision_id`` of each reported card (Unreal
per-machine memory test FR-MT-K-2, chunk 1).

``revision_id`` is *nullable on invalid* on its own: an absent or invalid
value is stored ``null`` and the card is kept, unlike an invalid
``vendor_id`` or ``device_id``, which drops the card.  Checked through the
real heartbeat endpoint, the stored row, the heartbeat answer, the admin
listing and the OpenAPI schema; one test sends the worker's real report
(``_card_contract_harness``: a real ``config.json``, faked probes).
"""

import json

import pytest
from django.test import Client

from sethlans_worker_agent.cards.os_probe import OsCard
from tests.unit.manager.card_report_helpers import KEY_2, card, report
from workers.models import Worker

from ._card_contract_harness import (
    KEY_LOST, KEY_OK, KEY_UNTICKED, NVIDIA, RTX, blender_record, record, stored_form, worker_machine,
)

HB = '/api/heartbeat/'
MAX_PCI_ID = 0xFFFF
INVALID_REVISIONS = [MAX_PCI_ID + 1, -1, '161', 'A1', True, False, 161.0, [161], {'rev': 161}]
INVALID_IDS = ['too-big', 'negative', 'string', 'hex-string', 'true', 'false', 'float', 'list', 'dict']


def _send(make_worker_client, hostname, *cards):
    """Post one heartbeat carrying ``cards``; return the stored card list (the answer must match it)."""
    worker, client, _token = make_worker_client(hostname)
    resp = client.post(HB, {'hostname': hostname, 'cards': report(cards=list(cards))}, format='json')
    assert resp.status_code == 200, resp.data
    stored = Worker.objects.get(pk=worker.pk).card_status['cards']
    assert resp.data['card_status']['cards'] == stored
    return stored


def _revisions(stored):
    return {entry['key']: entry['revision_id'] for entry in stored}


@pytest.mark.django_db
class TestRevisionIdStored:

    @pytest.mark.parametrize('revision_id', [0, 0xA1, 0xFF, MAX_PCI_ID], ids=['zero', 'a1', 'byte-max', 'pci-max'])
    def test_a_valid_revision_is_stored(self, make_worker_client, revision_id):
        stored = _send(make_worker_client, 'rev-ok', card(revision_id=revision_id))
        assert stored == [card(revision_id=revision_id)]

    def test_null_is_stored_null(self, make_worker_client):
        stored = _send(make_worker_client, 'rev-null', card(revision_id=None))
        assert stored == [card(revision_id=None)]

    def test_absent_is_stored_null(self, make_worker_client):
        """A worker from before FR-MT-K-2 sends no ``revision_id``: the card is kept with ``null``."""
        entry = card()
        del entry['revision_id']
        stored = _send(make_worker_client, 'rev-absent', entry)
        assert stored == [card(revision_id=None)]
        # Stored exactly like a card that sent ``revision_id``, key order included. The order is
        # the database's (MySQL JSON and PostgreSQL jsonb sort the keys; SQLite and MariaDB keep them).
        reference = _send(make_worker_client, 'rev-absent-ref', card(revision_id=None))
        assert list(stored[0]) == list(reference[0])

    def test_revisions_are_per_card(self, make_worker_client):
        stored = _send(make_worker_client, 'rev-two', card(revision_id=0xA1), card(key=KEY_2, revision_id=0xC8))
        assert _revisions(stored) == {card()['key']: 0xA1, KEY_2: 0xC8}

    def test_the_admin_listing_shows_the_revision(self, make_worker_client, admin_client):
        _send(make_worker_client, 'rev-list', card(revision_id=0xA1))
        row = next(w for w in admin_client.get(HB).data if w['hostname'] == 'rev-list')
        assert row['card_status']['cards'][0]['revision_id'] == 0xA1


@pytest.mark.django_db
class TestInvalidRevisionId:

    @pytest.mark.parametrize('revision_id', INVALID_REVISIONS, ids=INVALID_IDS)
    def test_an_invalid_revision_is_stored_null_and_the_card_kept(self, make_worker_client, revision_id):
        stored = _send(make_worker_client, 'rev-bad', card(revision_id=revision_id), card(key=KEY_2))
        assert stored == [card(revision_id=None), card(key=KEY_2)]

    def test_an_invalid_revision_changes_no_other_field(self, make_worker_client):
        sent = card(revision_id=MAX_PCI_ID + 1, job_id=42, renderer='UNREAL', screen=True)
        stored = _send(make_worker_client, 'rev-bad-alone', sent)
        assert stored == [dict(sent, revision_id=None)]

    @pytest.mark.parametrize('field', ['vendor_id', 'device_id'])
    @pytest.mark.parametrize('value', INVALID_REVISIONS, ids=INVALID_IDS)
    def test_the_same_value_as_vendor_or_device_id_drops_the_card(self, make_worker_client, field, value):
        """The difference holds: only ``revision_id`` is nullable on invalid."""
        stored = _send(make_worker_client, 'rev-vs-id', card(**{field: value}), card(key=KEY_2))
        assert stored == [card(key=KEY_2)]

    def test_a_valid_revision_does_not_save_a_card_with_an_invalid_vendor_id(self, make_worker_client):
        stored = _send(make_worker_client, 'rev-no-rescue',
                       card(vendor_id=MAX_PCI_ID + 1, revision_id=0xA1), card(key=KEY_2, revision_id='x'))
        assert stored == [card(key=KEY_2, revision_id=None)]


@pytest.fixture
def machine(monkeypatch, tmp_path):
    yield from worker_machine(monkeypatch, tmp_path)


@pytest.mark.django_db
def test_the_workers_real_report_stores_each_cards_revision(machine, make_worker_client):
    """Probed, not probed (``None``, as on macOS) and a lost card's saved record, worker -> heartbeat -> row."""
    machine.install(os_cards=[OsCard(key=KEY_OK, name=RTX, vendor_id=NVIDIA, device_id=0x2704, revision_id=0xA1),
                              OsCard(key=KEY_UNTICKED, name=RTX, vendor_id=NVIDIA, device_id=0x2704)],
                    blender_cards=[blender_record(KEY_OK, RTX, 'OPTIX'), blender_record(KEY_UNTICKED, RTX, 'OPTIX')])
    lost = dict(record(KEY_LOST, 'NVIDIA GeForce RTX 3090', NVIDIA, 0x2204), revision_id=0xC1)
    machine.seed({'method': 'gpu', 'gpu_mode': 'split', 'revision': 2, 'known': [KEY_OK, KEY_UNTICKED, KEY_LOST],
                  'cards': [record(KEY_OK, RTX, NVIDIA, 0x2704), lost], 'alerts': []})
    sent = machine.report()
    assert _revisions(sent['cards']) == {KEY_OK: 0xA1, KEY_UNTICKED: None, KEY_LOST: 0xC1}
    assert {entry['key']: entry['state'] for entry in sent['cards']}[KEY_LOST] == 'lost'

    worker, client, _token = make_worker_client('rev-real')
    resp = client.post(HB, {'hostname': 'rev-real', 'cards': sent}, format='json')

    assert resp.status_code == 200, resp.data
    assert Worker.objects.get(pk=worker.pk).card_status == stored_form(sent)


@pytest.mark.django_db
def test_openapi_lists_revision_id_on_worker_card_as_optional_and_nullable():
    resp = Client().get('/api/schema/?format=json', HTTP_ACCEPT='application/json')
    assert resp.status_code == 200
    schema = json.loads(resp.content.decode('utf-8'))['components']['schemas']['WorkerCard']
    assert schema['properties']['revision_id'] == {'type': 'integer', 'nullable': True}
    assert 'revision_id' not in schema.get('required', [])
