# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The worker's enrollment client against the real manager over TLS (setup-rebuild FR-WK-8).

Real parts: the worker's ``enrollment_client.enroll`` (key proof, envelope
check, certificate check, token decryption) over a real socket through
``PeerCertAdapter``, and the manager's ``POST /api/enroll/`` view with its URL
router, nonce store, rate limiter and the test database, served by Django's
live server with its listening socket wrapped in TLS on 127.0.0.1 (the
``tls_manager`` fixture, ``_tls_manager``).

Outside the parts, so set directly: the manager's ``runtime_state`` (its id
and the certificate fingerprint it signs) and its enrollment key row.

The enroll request is unpinned (``verify=False``) on purpose, so each call
raises urllib3's ``InsecureRequestWarning``; the tests expect it.
"""
import json

import pytest
from rest_framework.authtoken.models import Token
from urllib3.exceptions import InsecureRequestWarning

from sethlans_manager import runtime_state
from sethlans_worker_agent import enrollment_client
from tests.unit.worker._tls_loopback import _self_signed
from workers.models import Worker

from ._tls_manager import MANAGER_ID
from .conftest import TEST_ENROLLMENT_KEY_CANONICAL

KEY_AS_TYPED = '4F9X-K2PB-Q7M3-N8RT'  # TEST_ENROLLMENT_KEY_CANONICAL as a user types it
WRONG_KEY = '00000000000000PP'
HOSTNAME = 'tls-enroll-host'


@pytest.mark.parametrize('tls_manager', [True, False], ids=['keep-alive', 'connection-close'], indirect=True)
def test_enroll_over_tls_gets_a_working_token_and_never_sends_the_key(tls_manager, request, mocker):
    """FR-WK-8: real client + real view over TLS: token in the Token table, body is only hostname/nonce/proof.

    Both connection modes, since the client reads the server certificate at
    connect time so that a ``Connection: close`` answer cannot lose it.
    """
    post = mocker.spy(enrollment_client, '_post_enroll')

    with pytest.warns(InsecureRequestWarning):
        result = enrollment_client.enroll(tls_manager.base_url, KEY_AS_TYPED, HOSTNAME)

    assert result['cert_fingerprint'] == tls_manager.fingerprint
    assert result['manager_id'] == MANAGER_ID
    assert Token.objects.get(key=result['api_token']).user == Worker.objects.get(hostname=HOSTNAME).user
    wire = post.spy_return.request.body.decode('ascii')
    assert set(json.loads(wire)) == {'hostname', 'nonce', 'proof'}
    assert TEST_ENROLLMENT_KEY_CANONICAL not in wire and KEY_AS_TYPED not in wire
    closes = post.spy_return.headers.get('Connection', '').lower() == 'close'
    assert closes is (request.node.callspec.id == 'connection-close')


def test_enroll_refuses_a_certificate_other_than_the_signed_one(tls_manager, monkeypatch, mocker, tmp_path):
    """FR-WK-8 certificate check: the manager signs fingerprint A, the connection shows B (relay): no result."""
    other = tmp_path / 'other'
    other.mkdir()
    monkeypatch.setattr(runtime_state, 'cert_fingerprint', _self_signed(other)[2])
    post = mocker.spy(enrollment_client, '_post_enroll')

    with pytest.raises(enrollment_client.CertificateMismatchError, match='possible relay'), \
            pytest.warns(InsecureRequestWarning):
        enrollment_client.enroll(tls_manager.base_url, KEY_AS_TYPED, HOSTNAME)

    assert post.spy_return.status_code == 200  # the manager answered; the worker refused it


def test_enroll_with_wrong_key_is_refused_and_does_not_burn_the_nonce(
        tls_manager, reset_enroll_module_state, mocker):
    """FR-WK-8: a wrong key gives 403 -> InvalidKeyError, and the proof is checked before the nonce is recorded."""
    post = mocker.spy(enrollment_client, '_post_enroll')

    with pytest.raises(enrollment_client.InvalidKeyError), pytest.warns(InsecureRequestWarning):
        enrollment_client.enroll(tls_manager.base_url, WRONG_KEY, HOSTNAME)

    assert post.spy_return.status_code == 403
    nonce = post.call_args.args[1]['nonce']
    with reset_enroll_module_state._nonce_store._lock:
        assert nonce not in reset_enroll_module_state._nonce_store._seen
    assert not Worker.objects.filter(hostname=HOSTNAME).exists()
