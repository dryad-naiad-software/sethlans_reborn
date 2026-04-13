# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``ui_cert_fingerprint`` handling in the heartbeat
endpoint (spec FR-15, FR-16, FR-16a).

Covers:
- Valid fingerprint stored via heartbeat and full registration
- Invalid fingerprints (wrong length, non-hex, non-string) rejected
  with ``logger.warning`` and stored as ``''``
- Missing fingerprint → backward compat (stored as ``''``)
- ``WorkerSerializer`` includes ``ui_cert_fingerprint`` in responses
- ``ui_cert_fingerprint`` is read-only (cannot be set via PATCH)
"""

import pytest

from workers.models import Worker

HEARTBEAT_URL = '/api/heartbeat/'

# A valid SHA-256 fingerprint: 64 lowercase hex characters
VALID_FINGERPRINT = 'ab' * 32  # 64 chars


def _full_heartbeat(client, hostname, extra=None):
    """Send a full-registration heartbeat (with ``os`` set)."""
    data = {
        'hostname': hostname,
        'os': 'Linux',
        'ip_address': '10.0.0.1',
        'available_tools': {},
    }
    if extra:
        data.update(extra)
    return client.post(HEARTBEAT_URL, data=data, format='json')


def _simple_heartbeat(client, hostname, extra=None):
    """Send a simple heartbeat (no ``os`` — routes to _handle_heartbeat)."""
    data = {'hostname': hostname}
    if extra:
        data.update(extra)
    return client.post(HEARTBEAT_URL, data=data, format='json')


@pytest.mark.django_db
class TestFingerprintViaFullRegistration:
    """FR-16a: ``_validate_ui_cert_fingerprint`` used in registration."""

    def test_valid_fingerprint_stored(
        self, make_worker_client, default_version,
    ):
        """Full registration with valid 64-char hex fingerprint stores it."""
        _, client, _ = make_worker_client('fp-reg-valid')
        resp = _full_heartbeat(client, 'fp-reg-valid', {
            'ui_cert_fingerprint': VALID_FINGERPRINT,
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='fp-reg-valid')
        assert worker.ui_cert_fingerprint == VALID_FINGERPRINT

    def test_missing_fingerprint_stores_empty(
        self, make_worker_client, default_version,
    ):
        """Full registration without fingerprint → backward compat ''."""
        _, client, _ = make_worker_client('fp-reg-missing')
        resp = _full_heartbeat(client, 'fp-reg-missing')
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='fp-reg-missing')
        assert worker.ui_cert_fingerprint == ''

    def test_invalid_short_fingerprint_rejected(
        self, make_worker_client, default_version,
    ):
        """Too-short fingerprint rejected and stored as ''."""
        _, client, _ = make_worker_client('fp-reg-short')
        resp = _full_heartbeat(client, 'fp-reg-short', {
            'ui_cert_fingerprint': 'abcd1234',
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='fp-reg-short')
        assert worker.ui_cert_fingerprint == ''

    def test_non_hex_fingerprint_rejected(
        self, make_worker_client, default_version,
    ):
        """Fingerprint with non-hex chars rejected and stored as ''."""
        _, client, _ = make_worker_client('fp-reg-nonhex')
        bad_fp = 'zz' * 32  # correct length but invalid chars
        resp = _full_heartbeat(client, 'fp-reg-nonhex', {
            'ui_cert_fingerprint': bad_fp,
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='fp-reg-nonhex')
        assert worker.ui_cert_fingerprint == ''


@pytest.mark.django_db
class TestFingerprintViaHeartbeat:
    """FR-16a: ``_validate_ui_cert_fingerprint`` used in heartbeat."""

    def test_valid_fingerprint_stored_on_heartbeat(
        self, make_worker_client, default_version,
    ):
        """Simple heartbeat with valid fingerprint stores it."""
        _, client, _ = make_worker_client('fp-hb-valid')
        resp = _simple_heartbeat(client, 'fp-hb-valid', {
            'ui_cert_fingerprint': VALID_FINGERPRINT,
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='fp-hb-valid')
        assert worker.ui_cert_fingerprint == VALID_FINGERPRINT

    def test_missing_fingerprint_stores_empty_on_heartbeat(
        self, make_worker_client, default_version,
    ):
        """Heartbeat without fingerprint → stored as ''."""
        _, client, _ = make_worker_client('fp-hb-missing')
        resp = _simple_heartbeat(client, 'fp-hb-missing')
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='fp-hb-missing')
        assert worker.ui_cert_fingerprint == ''

    def test_non_string_fingerprint_rejected(
        self, make_worker_client, default_version,
    ):
        """Non-string (integer) fingerprint rejected and stored as ''."""
        _, client, _ = make_worker_client('fp-hb-int')
        resp = _simple_heartbeat(client, 'fp-hb-int', {
            'ui_cert_fingerprint': 12345,
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='fp-hb-int')
        assert worker.ui_cert_fingerprint == ''

    def test_uppercase_hex_fingerprint_rejected(
        self, make_worker_client, default_version,
    ):
        """Uppercase hex fingerprint rejected (regex requires lowercase)."""
        _, client, _ = make_worker_client('fp-hb-upper')
        upper_fp = 'AB' * 32
        resp = _simple_heartbeat(client, 'fp-hb-upper', {
            'ui_cert_fingerprint': upper_fp,
        })
        assert resp.status_code == 200
        worker = Worker.objects.get(hostname='fp-hb-upper')
        assert worker.ui_cert_fingerprint == ''

    def test_fingerprint_updated_on_subsequent_heartbeat(
        self, make_worker_client, default_version,
    ):
        """Fingerprint is updated when worker sends a new one."""
        _, client, _ = make_worker_client('fp-hb-update')
        fp1 = 'aa' * 32
        fp2 = 'bb' * 32
        _simple_heartbeat(client, 'fp-hb-update', {
            'ui_cert_fingerprint': fp1,
        })
        worker = Worker.objects.get(hostname='fp-hb-update')
        assert worker.ui_cert_fingerprint == fp1

        _simple_heartbeat(client, 'fp-hb-update', {
            'ui_cert_fingerprint': fp2,
        })
        worker.refresh_from_db()
        assert worker.ui_cert_fingerprint == fp2


@pytest.mark.django_db
class TestFingerprintInSerializer:
    """FR-16: ``ui_cert_fingerprint`` appears in serializer output."""

    def test_list_response_includes_fingerprint(
        self, make_worker_client, admin_client, default_version,
    ):
        """GET /api/heartbeat/ includes ui_cert_fingerprint in response."""
        _, client, _ = make_worker_client('fp-list')
        _full_heartbeat(client, 'fp-list', {
            'ui_cert_fingerprint': VALID_FINGERPRINT,
        })
        resp = admin_client.get(HEARTBEAT_URL)
        assert resp.status_code == 200
        match = [w for w in resp.data if w['hostname'] == 'fp-list']
        assert len(match) == 1
        assert match[0]['ui_cert_fingerprint'] == VALID_FINGERPRINT

    def test_fingerprint_is_read_only(
        self, make_worker_client, admin_client, default_version,
    ):
        """ui_cert_fingerprint cannot be set via serializer (read-only).

        The field is in ``read_only_fields``, so a direct PATCH or PUT
        through the serializer would ignore it. We verify the field is
        listed in the serializer's ``read_only_fields``.
        """
        from workers.serializers import WorkerSerializer
        meta = WorkerSerializer.Meta
        assert 'ui_cert_fingerprint' in meta.fields
        assert 'ui_cert_fingerprint' in meta.read_only_fields
