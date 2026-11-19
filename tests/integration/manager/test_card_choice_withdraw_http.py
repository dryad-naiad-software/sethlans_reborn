# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for ``DELETE /api/heartbeat/{pk}/card-choice/`` through
real HTTP (worker card selection FR-CM-11, FR-CM-15, R5, AC-30, AC-34,
AC-40).

A real client sends a DELETE without a body and without a
``Content-Length`` header; the action must not ask for one.  The live
server is pytest-django's (it shares the in-memory test database).
"""

import http.client
import json
from urllib.parse import urlsplit

import pytest
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token

from workers.models import Worker
from workers.services import card_choice

from .card_choice_helpers import body, two_cards, url


def _connect(live_server):
    # 127.0.0.1, not ``localhost``: on Windows ``localhost`` tries ::1 first and waits.
    return http.client.HTTPConnection('127.0.0.1', urlsplit(live_server.url).port, timeout=10)


def _request(live_server, method, path, token, payload=None):
    """Send one request over a socket; return ``(status, parsed body or None)``.

    ``http.client`` sends no ``Content-Length`` for a DELETE without a body.
    """
    conn = _connect(live_server)
    headers = {'Authorization': f'Token {token}'}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
    try:
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else None)
    finally:
        conn.close()


@pytest.fixture
def admin_token(transactional_db):
    user = get_user_model().objects.create_user(username='http-admin', password='pw-12345678', is_staff=True)
    return user, Token.objects.create(user=user).key


@pytest.fixture
def worker(transactional_db):
    return Worker.objects.create(hostname='http-host', card_status=two_cards())


@pytest.mark.django_db(transaction=True)
class TestWithdrawOverHttp:

    def test_delete_without_body_withdraws_then_409(self, live_server, admin_token, worker, mocker):
        user, token = admin_token
        info = mocker.patch.object(card_choice.logger, 'info')
        status, sent = _request(live_server, 'POST', url(worker.pk), token, body())
        assert status == 202, sent
        request_id = sent['card_choice_request']['id']
        status, data = _request(live_server, 'DELETE', url(worker.pk), token)
        assert (status, data) == (204, None)
        stored = Worker.objects.get(pk=worker.pk)
        assert stored.card_choice_request == {}
        assert [entry['id'] for entry in stored.card_choice_issued] == [request_id]
        lines = [c.args[0] % c.args[1:] for c in info.call_args_list]
        assert f'Graphics card change {request_id} for worker http-host withdrawn by user id={user.pk}' in lines
        status, data = _request(live_server, 'DELETE', url(worker.pk), token)
        assert (status, data) == (409, {'detail': card_choice.NOTHING_PENDING})

    def test_unknown_worker_is_404(self, live_server, admin_token, worker):
        _user, token = admin_token
        status, data = _request(live_server, 'DELETE', url(worker.pk + 1000), token)
        assert (status, data) == (404, {'detail': 'Worker not found.'})

    def test_post_with_chunked_body_and_no_length_is_refused(self, live_server, admin_token, worker):
        """A POST without ``Content-Length`` never reaches the parser (R5).

        The test live server passes a chunked body through without a length,
        so this is 411.  Waitress, in production, de-chunks it and sets
        ``Content-Length``, so there it meets the 8 KiB cap (413) instead.
        """
        _user, token = admin_token
        conn = _connect(live_server)
        try:
            conn.request('POST', url(worker.pk), body=iter([json.dumps(body()).encode()]),
                         headers={'Authorization': f'Token {token}', 'Content-Type': 'application/json'},
                         encode_chunked=True)
            resp = conn.getresponse()
            status, raw = resp.status, resp.read()
        finally:
            conn.close()
        assert status == 411, raw
        assert json.loads(raw) == {'detail': card_choice.LENGTH_REQUIRED}
        assert Worker.objects.get(pk=worker.pk).card_choice_request == {}
