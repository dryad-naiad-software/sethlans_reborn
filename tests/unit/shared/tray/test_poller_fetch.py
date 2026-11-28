# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""``QtStatePoller._fetch``: the loopback status request uses no proxy, follows no
redirect and reads at most 64 KiB (6c security review L3)."""

from __future__ import annotations

import http.server
import json
import threading

import pytest

pytest.importorskip("PySide6", reason="PySide6 required for poller")

from shared.tray import poller as poller_mod  # noqa: E402
from shared.tray.poller import QtStatePoller  # noqa: E402


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server's name
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
            return
        body = (json.dumps({"boot_id": "b1", "workers_online": 2}).encode() if self.path == "/ok"
                else b" " * (poller_mod.MAX_STATUS_BYTES + 1))
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def status_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _fetch(url):
    return QtStatePoller(url, threading.Event(), threading.Event())._fetch()


def test_status_is_read_without_a_proxy(status_server, monkeypatch):
    """A proxy in the environment is not used for the loopback request."""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    assert _fetch(f"{status_server}/ok")["workers_online"] == 2


@pytest.mark.parametrize(("path", "error"), [("/redirect", "HTTP 302"), ("/big", "larger than")])
def test_redirect_and_oversized_answer_are_refused(status_server, path, error):
    """A redirect is not followed and an answer over 64 KiB is not read."""
    with pytest.raises(RuntimeError, match=error):
        _fetch(f"{status_server}{path}")
