# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The worker's one enrollment path (``setup_cli.enroll``; setup-rebuild FR-WK-1, FR-WK-6).

``enrollment_client.enroll`` is replaced: these tests cover the manager URL
the setup form gives, the API URL and ``config.json`` pairs built from an
answer, and the ``error`` code of each enrollment failure.
"""
import pytest
import requests

from sethlans_worker_agent import enrollment_client
from sethlans_worker_agent.setup_cli import enroll

ANSWER = {'api_token': 't' * 40, 'cert_fingerprint': 'ab' * 32, 'manager_id': '00000000-0000-0000-0000-000000000042'}


class TestManagerUrl:

    @pytest.mark.parametrize('text, expected', [
        ('https://manager.example', ('manager.example', 8080)),
        (' https://manager.example:9443/ ', ('manager.example', 9443)),
        ('https://10.0.0.5:8080/api/', ('10.0.0.5', 8080)),
        ('https://[fd00::5]:8443', ('fd00::5', 8443)),
    ], ids=['default-port', 'port-and-slash', 'api-path', 'ipv6'])
    def test_accepted(self, text, expected):
        assert enroll.parse_manager_url(text) == expected

    @pytest.mark.parametrize('text', [
        'http://manager.example', 'https://manager.example:0', 'https://manager.example:65536',
        'https://user:pw@manager.example', 'https://manager.example/other', 'https://manager.example?x=1',
        'https://', '', None, 'https://' + 'a' * 2048,
    ], ids=['http', 'port-0', 'port-65536', 'credentials', 'other-path', 'query', 'no-host', 'empty', 'null',
            'too-long'])
    def test_refused_with_one_sentence(self, text):
        with pytest.raises(ValueError) as caught:
            enroll.parse_manager_url(text)
        assert str(caught.value).endswith('.')


@pytest.mark.parametrize('name_args, stored_name', [((), None), (('Studio',), 'Studio')],
                         ids=['no-name', 'name-given'])
def test_enroll_pairs_builds_the_api_url_and_the_pairs(mocker, name_args, stored_name):
    """FR-WK-1, FR-RS-3: one enrollment gives six ``config.json`` pairs; ``manager.name`` is always written (the
    name given, else ``None``, so an earlier manager's name is never kept); an IPv6 host goes in brackets."""
    call = mocker.patch.object(enrollment_client, 'enroll', return_value=dict(ANSWER, status='pending'))
    answer, pairs = enroll.enroll_pairs('fd00::5', 8443, 'KEY-TEXT', 'render-node', *name_args)
    call.assert_called_once_with('https://[fd00::5]:8443/api/', 'KEY-TEXT', 'render-node')
    assert answer['status'] == 'pending'
    assert pairs == [('manager.api_token', ANSWER['api_token']), ('manager.cert_fingerprint', ANSWER['cert_fingerprint']),
                     ('manager.manager_id', ANSWER['manager_id']), ('manager.host', 'fd00::5'), ('manager.port', 8443),
                     ('manager.name', stored_name)]


def _unreachable():
    error = enrollment_client.EnrollmentError('Could not reach the manager.')
    error.__cause__ = requests.exceptions.ConnectionError('refused')
    return error


@pytest.mark.parametrize('error, code', [
    (enrollment_client.InvalidKeyError('x'), 'invalid_key'),
    (enrollment_client.CertificateMismatchError('x'), 'certificate_mismatch'),
    (enrollment_client.RateLimitedError('x'), 'rate_limited'),
    (enrollment_client.ServiceUnavailableError('x'), 'manager_not_ready'),
    (enrollment_client.InvalidHostnameError('x'), 'invalid_hostname'),
    (_unreachable(), 'unreachable'),
    (enrollment_client.HmacMismatchError('x'), 'enrollment_failed'),
    (enrollment_client.EnrollmentError('x'), 'enrollment_failed'),
], ids=['invalid-key', 'certificate', 'rate-limited', 'not-ready', 'hostname', 'unreachable', 'subclass-other',
        'plain-base'])
def test_error_code_of_each_enrollment_failure(error, code):
    """FR-WK-1: each enrollment error class maps to its ``error`` code; anything else is ``enrollment_failed``."""
    assert enroll.error_code(error) == code
