# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Edges of a real setup process, directly on Waitress and behind Caddy.

* A host name that is not one of this machine's gets a JSON 400 with the
  security headers.
* ``HEAD /api/health/`` answers 200.
* A POST of about 70 KB gets 413 promptly (the client gives up after a
  few seconds, so a stall fails the test instead of hanging it).
* A data folder below a junction the test made is refused (Windows).
"""
import json
import os
import sys
import time
from http.client import HTTPConnection

import pytest

from shared import owner_only_acl

from .caddy_front import client_context
from .setup_process import Client, fields_of, manager_answers, request

SECURITY_HEADERS = {
    "x-frame-options": "DENY",
    "content-security-policy": "frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}
BAD_HOST = {"error": "bad_host", "message": "This host name is not one of this machine's names."}
OVERSIZE_BYTES = 70 * 1024
PROMPT_S = 10.0


def _assert_security_headers(resp, api=True):
    for name, value in SECURITY_HEADERS.items():
        assert resp.headers.get(name) == value, (name, resp.headers)
    if api:
        assert resp.headers.get("cache-control") == "no-store"


def _oversize_auth(port, ssl_context=None):
    body = {"token": "x" * OVERSIZE_BYTES}
    return request(port, "POST", "/api/setup/auth/", body, ssl_context=ssl_context, timeout=PROMPT_S)


def _oversize_status_direct(port):
    """Status and elapsed time of an oversize POST straight to Waitress.

    Waitress answers 413 and closes without reading the rest of the body,
    so the client's TCP stack often gets a reset before it has read the
    413's body (Caddy, in front in real use, reads it whole). The status
    line and headers always arrive first; only those are checked here.
    """
    conn = HTTPConnection("127.0.0.1", port, timeout=PROMPT_S)
    start = time.monotonic()
    try:
        conn.request("POST", "/api/setup/auth/", body=json.dumps({"token": "x" * OVERSIZE_BYTES}).encode(),
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        try:
            resp.read()
        except ConnectionResetError:
            pass
        return resp.status, time.monotonic() - start
    finally:
        conn.close()


@pytest.fixture
def own_junction(tmp_path):
    """``(target, junction)``: a junction this test's user made; removed (not its target) after."""
    import _winapi

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "junction"
    _winapi.CreateJunction(str(real), str(link))
    try:
        if owner_only_acl.is_owned_by_system(link):
            pytest.skip("elevated session: the junction is owned by Administrators, which the rules count as the system")
        yield real, link
    finally:
        os.rmdir(link)


@pytest.fixture
def behind_caddy(setup_process, caddy_front):
    front = caddy_front()
    proc = setup_process(setup_port=front.port)
    front.start(proc.waitress_port)
    return proc, front, client_context(front.cert)


class TestSetupHttpEdgesIntegration:
    def test_bad_host_gets_json_400_with_headers_direct(self, setup_process):
        proc = setup_process()

        resp = request(proc.waitress_port, "GET", "/api/health/", headers={"Host": "evil.example"})

        assert resp.status == 400, resp.raw
        assert resp.headers["content-type"].startswith("application/json")
        assert resp.json() == BAD_HOST
        _assert_security_headers(resp)
        log = proc.log_path.read_text(encoding="utf-8", errors="replace")
        assert "evil.example" in log

    def test_bad_host_gets_json_400_with_headers_behind_caddy(self, behind_caddy):
        proc, front, context = behind_caddy

        resp = request(front.port, "POST", "/api/setup/auth/", {"token": proc.token},
                       headers={"Host": "evil.example"}, ssl_context=context)

        assert resp.status == 400, resp.raw
        assert resp.json() == BAD_HOST
        _assert_security_headers(resp)
        assert "set-cookie" not in resp.headers  # the right token did not open a session

    def test_head_on_health_answers_200(self, setup_process):
        proc = setup_process()

        resp = request(proc.waitress_port, "HEAD", "/api/health/")

        assert resp.status == 200
        assert resp.raw == b""
        assert resp.headers["content-type"].startswith("application/json")
        _assert_security_headers(resp)

    def test_oversize_post_gets_413_promptly_direct(self, setup_process):
        proc = setup_process()

        for _ in range(5):
            status, elapsed = _oversize_status_direct(proc.waitress_port)
            assert (status, elapsed < PROMPT_S / 2) == (413, True), elapsed

        assert proc.proc.poll() is None
        assert Client(proc.waitress_port).auth(proc.token).status == 200  # still serving

    def test_oversize_post_gets_413_promptly_behind_caddy(self, behind_caddy):
        proc, front, context = behind_caddy

        for _ in range(5):  # Caddy delivers the whole 413 every time
            resp = _oversize_auth(front.port, context)
            assert resp.status == 413, resp.raw
            assert b"Too Large" in resp.raw
        assert Client(front.port, ssl_context=context).auth(proc.token).status == 200

    @pytest.mark.skipif(sys.platform != "win32", reason="junctions are a Windows feature")
    def test_data_folder_below_own_junction_is_refused(self, setup_process, own_junction):
        real, link = own_junction
        proc = setup_process()
        client = Client(proc.waitress_port)
        assert client.auth(proc.token).status == 200

        resp = client.submit(manager_answers(link / "data", proc.setup_port))

        messages = fields_of(resp)["data_folder.path"]
        expected = f"{link} is a link or junction above the data folder; give the folder's real path."
        assert expected in messages, messages
        assert not (real / "data").exists()
        assert proc.proc.poll() is None
