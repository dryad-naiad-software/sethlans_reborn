# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The wrong-token limit of a real setup process keys on the real client.

* Directly on Waitress, a request from 127.0.0.1 carrying
  ``X-Forwarded-For`` (what Caddy sends) is counted for the forwarded
  address: a LAN address at its limit does not block loopback.
* Behind Caddy, a client reaching it on this machine's LAN address is
  counted for that address, and loopback still gets in.
* Behind Caddy, a forged ``X-Forwarded-For`` does not pick the bucket.
"""
import pytest

from .caddy_front import client_context, lan_address
from .setup_process import Client, request

MAX_FAILURES = 10
LAN_CLIENT = "192.0.2.10"
OTHER_LAN_CLIENT = "192.0.2.11"
FORGED_CLIENT = "192.0.2.77"


def _auth(port, token, *, host="127.0.0.1", ssl_context=None, forwarded=None):
    headers = {"X-Forwarded-For": forwarded} if forwarded else None
    return request(port, "POST", "/api/setup/auth/", {"token": token}, headers, host=host,
                   ssl_context=ssl_context)


def _use_up_limit(port, *, host="127.0.0.1", ssl_context=None, forwarded=None):
    for attempt in range(MAX_FAILURES):
        resp = _auth(port, f"wrong-{attempt}", host=host, ssl_context=ssl_context, forwarded=forwarded)
        assert resp.status == 403, (attempt, resp.raw)
        assert resp.json()["error"] == "invalid_token"


def _assert_limited(resp):
    assert resp.status == 429, resp.raw
    assert resp.json()["error"] == "too_many_attempts"
    assert resp.headers.get("retry-after") == "60"


def _log(proc) -> str:
    return proc.log_path.read_text(encoding="utf-8", errors="replace")


class TestSetupRateLimitIntegration:
    def test_forwarded_lan_client_at_limit_does_not_block_loopback(self, setup_process):
        proc = setup_process()
        port = proc.waitress_port
        _use_up_limit(port, forwarded=LAN_CLIENT)

        _assert_limited(_auth(port, proc.token, forwarded=LAN_CLIENT))
        assert _auth(port, proc.token, forwarded=OTHER_LAN_CLIENT).status == 200
        assert _auth(port, proc.token).status == 200
        assert f"Wrong setup token from {LAN_CLIENT}" in _log(proc)

    def test_limit_holds_even_with_the_right_token_and_counts_wrong_tokens_only(self, setup_process):
        proc = setup_process()
        port = proc.waitress_port
        for _ in range(MAX_FAILURES + 2):  # right tokens are not counted
            assert _auth(port, proc.token, forwarded=LAN_CLIENT).status == 200
        _use_up_limit(port, forwarded=LAN_CLIENT)

        _assert_limited(_auth(port, proc.token, forwarded=LAN_CLIENT))
        _assert_limited(_auth(port, "wrong", forwarded=LAN_CLIENT))

    def test_behind_caddy_lan_client_at_limit_does_not_block_loopback(self, setup_process, caddy_front):
        lan = lan_address()
        if lan is None:
            pytest.skip("this machine has no reachable non-loopback IPv4 address")
        front = caddy_front()
        proc = setup_process(setup_port=front.port)
        front.start(proc.waitress_port)
        context = client_context(front.cert)
        _use_up_limit(front.port, host=lan, ssl_context=context)

        _assert_limited(_auth(front.port, proc.token, host=lan, ssl_context=context))
        loopback = Client(front.port, ssl_context=context)
        assert loopback.auth(proc.token).status == 200
        assert loopback.call("GET", "/api/setup/form/").status == 200
        log = _log(proc)
        assert f"Wrong setup token from {lan}" in log
        assert "Wrong setup token from 127.0.0.1" not in log

    def test_behind_caddy_a_forged_forwarded_for_does_not_pick_the_bucket(self, setup_process, caddy_front):
        front = caddy_front()
        proc = setup_process(setup_port=front.port)
        front.start(proc.waitress_port)
        context = client_context(front.cert)
        _use_up_limit(front.port, ssl_context=context, forwarded=FORGED_CLIENT)

        # The failures were counted for the real client, 127.0.0.1.
        _assert_limited(_auth(front.port, proc.token, ssl_context=context))
        log = _log(proc)
        assert FORGED_CLIENT not in log
        assert "Wrong setup token from 127.0.0.1" in log
