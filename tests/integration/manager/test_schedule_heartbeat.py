# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for heartbeat schedule payload processing.

Covers: Worker.schedule_config DB update from heartbeat ``schedule``
key, unrecognized key stripping, non-dict rejection, and serializer
output inclusion.

Spec references: FR-10a, FR-10b.
"""

import pytest

HEARTBEAT_URL = '/api/heartbeat/'


def _heartbeat_payload(hostname, **extra):
    """Return a minimal heartbeat payload with optional extras."""
    data = {'hostname': hostname}
    data.update(extra)
    return data


@pytest.mark.django_db
class TestScheduleConfigUpdate:
    """Heartbeat with ``schedule`` dict updates Worker.schedule_config."""

    def test_schedule_stored_in_db(self, worker_with_token):
        worker, client = worker_with_token
        schedule = {
            'enabled': True,
            'days': ['mon', 'tue', 'wed'],
            'start': '18:00',
            'end': '07:00',
            'timezone': 'America/New_York',
            'overrides_idle_detection': False,
        }
        resp = client.post(
            HEARTBEAT_URL,
            data=_heartbeat_payload(
                worker.hostname,
                schedule=schedule,
            ),
            format='json',
        )
        assert resp.status_code == 200

        worker.refresh_from_db()
        assert worker.schedule_config == schedule

    def test_schedule_overwrites_previous(self, worker_with_token):
        worker, client = worker_with_token
        # First heartbeat sets schedule
        client.post(
            HEARTBEAT_URL,
            data=_heartbeat_payload(
                worker.hostname,
                schedule={'enabled': True, 'start': '20:00'},
            ),
            format='json',
        )
        # Second heartbeat replaces it
        new_schedule = {'enabled': False}
        client.post(
            HEARTBEAT_URL,
            data=_heartbeat_payload(
                worker.hostname,
                schedule=new_schedule,
            ),
            format='json',
        )
        worker.refresh_from_db()
        assert worker.schedule_config == new_schedule

    def test_heartbeat_without_schedule_preserves_existing(
        self, worker_with_token,
    ):
        worker, client = worker_with_token
        # Set initial schedule
        worker.schedule_config = {'enabled': True, 'start': '18:00'}
        worker.save(update_fields=['schedule_config'])

        # Heartbeat without schedule key
        client.post(
            HEARTBEAT_URL,
            data=_heartbeat_payload(worker.hostname),
            format='json',
        )
        worker.refresh_from_db()
        assert worker.schedule_config == {
            'enabled': True,
            'start': '18:00',
        }


@pytest.mark.django_db
class TestScheduleKeyStripping:
    """Unrecognized keys are silently dropped (FR-10b)."""

    def test_unrecognized_keys_dropped(self, worker_with_token):
        worker, client = worker_with_token
        schedule_with_extra = {
            'enabled': True,
            'days': ['sat', 'sun'],
            'bogus_key': 'should be dropped',
            'another_bad': 42,
        }
        resp = client.post(
            HEARTBEAT_URL,
            data=_heartbeat_payload(
                worker.hostname,
                schedule=schedule_with_extra,
            ),
            format='json',
        )
        assert resp.status_code == 200

        worker.refresh_from_db()
        assert 'bogus_key' not in worker.schedule_config
        assert 'another_bad' not in worker.schedule_config
        assert worker.schedule_config == {
            'enabled': True,
            'days': ['sat', 'sun'],
        }

    def test_only_recognized_keys_stored(self, worker_with_token):
        worker, client = worker_with_token
        schedule = {
            'enabled': False,
            'days': ['mon'],
            'start': '09:00',
            'end': '17:00',
            'timezone': 'UTC',
            'overrides_idle_detection': True,
            'secret_field': 'nope',
        }
        client.post(
            HEARTBEAT_URL,
            data=_heartbeat_payload(
                worker.hostname,
                schedule=schedule,
            ),
            format='json',
        )
        worker.refresh_from_db()
        stored_keys = set(worker.schedule_config.keys())
        recognized = {
            'enabled', 'days', 'start', 'end',
            'timezone', 'overrides_idle_detection',
        }
        assert stored_keys <= recognized


@pytest.mark.django_db
class TestScheduleNonDictRejection:
    """Non-dict schedule payload is ignored (not stored)."""

    def test_string_schedule_ignored(self, worker_with_token):
        worker, client = worker_with_token
        worker.schedule_config = {'enabled': True}
        worker.save(update_fields=['schedule_config'])

        resp = client.post(
            HEARTBEAT_URL,
            data=_heartbeat_payload(
                worker.hostname,
                schedule='not-a-dict',
            ),
            format='json',
        )
        assert resp.status_code == 200
        worker.refresh_from_db()
        # Existing config preserved
        assert worker.schedule_config == {'enabled': True}

    def test_list_schedule_ignored(self, worker_with_token):
        worker, client = worker_with_token
        worker.schedule_config = {}
        worker.save(update_fields=['schedule_config'])

        resp = client.post(
            HEARTBEAT_URL,
            data=_heartbeat_payload(
                worker.hostname,
                schedule=[1, 2, 3],
            ),
            format='json',
        )
        assert resp.status_code == 200
        worker.refresh_from_db()
        assert worker.schedule_config == {}

    def test_integer_schedule_ignored(self, worker_with_token):
        worker, client = worker_with_token
        resp = client.post(
            HEARTBEAT_URL,
            data=_heartbeat_payload(
                worker.hostname,
                schedule=42,
            ),
            format='json',
        )
        assert resp.status_code == 200
        worker.refresh_from_db()
        assert worker.schedule_config == {}


@pytest.mark.django_db
class TestScheduleInSerializerOutput:
    """Worker serializer output includes schedule_config."""

    def test_schedule_config_in_worker_list(
        self, worker_with_token,
    ):
        worker, client = worker_with_token
        schedule = {'enabled': True, 'start': '22:00', 'end': '06:00'}
        worker.schedule_config = schedule
        worker.save(update_fields=['schedule_config'])

        resp = client.get(HEARTBEAT_URL)
        assert resp.status_code == 200
        worker_data = next(
            w for w in resp.data if w['id'] == worker.pk
        )
        assert worker_data['schedule_config'] == schedule

    def test_empty_schedule_in_serializer(
        self, worker_with_token,
    ):
        worker, client = worker_with_token
        resp = client.get(HEARTBEAT_URL)
        assert resp.status_code == 200
        worker_data = next(
            w for w in resp.data if w['id'] == worker.pk
        )
        assert worker_data['schedule_config'] == {}
