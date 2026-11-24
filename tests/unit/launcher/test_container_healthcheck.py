# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for :mod:`launcher.container_healthcheck` (issue #298).

The image's HEALTHCHECK must probe the port Caddy listens on, resolved
exactly as the container entrypoint resolves it, ignore proxy settings,
and treat a redirect as unhealthy. Redirect and proxy behaviour are
checked against a plain-HTTP server on loopback (the opener is the same
for http and https); everything else uses fakes.
"""

from __future__ import annotations

import http.client
import http.server
import threading
import urllib.error
from unittest.mock import MagicMock

import pytest

from launcher import container_entrypoint as ce
from launcher import container_healthcheck as hc


@pytest.fixture
def ini(monkeypatch, tmp_path):
    """No inherited port env; the settings file in tmp_path."""
    monkeypatch.delenv("SETHLANS_MANAGER_PORT", raising=False)
    path = tmp_path / "sethlans.ini"
    monkeypatch.setattr(hc, "get_config_file_path", lambda: path)
    return path


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        if self.path == "/redirect/":
            self.send_response(302)
            self.send_header("Location", "/ok/")
        else:
            self.send_response(200 if self.path == "/ok/" else 404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args):
        pass


@pytest.fixture
def server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


def test_uses_the_entrypoints_port_resolver():
    assert hc.resolve_public_port is ce.resolve_public_port


def test_default_port_is_8080(ini):
    assert hc.health_url() == "https://127.0.0.1:8080/api/health/"


def test_port_from_env(ini, monkeypatch):
    monkeypatch.setenv("SETHLANS_MANAGER_PORT", "9443")
    assert hc.health_url() == "https://127.0.0.1:9443/api/health/"


def test_port_from_manager_ini(ini):
    ini.write_text("[server]\nport = 8443\n", encoding="utf-8")
    assert hc.health_url() == "https://127.0.0.1:8443/api/health/"


def test_probe_ok_on_200(server):
    assert hc.probe(f"{server}/ok/") is True


def test_redirect_is_unhealthy(server):
    assert hc.probe(f"{server}/redirect/") is False


def test_non_200_is_unhealthy(server):
    assert hc.probe(f"{server}/missing/") is False


def test_proxy_env_is_ignored(server, monkeypatch):
    # A proxy that would refuse every connection; the probe must not use it.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    assert hc.probe(f"{server}/ok/") is True


def test_opener_has_no_proxies_and_no_redirects():
    import ssl
    import urllib.request

    opener = hc.build_opener(ssl.create_default_context())
    proxies = [h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
    redirects = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
    # ProxyHandler({}) has no protocol hooks, so it is not kept, and its
    # presence stops build_opener adding the env-reading default one.
    assert proxies == []
    assert len(redirects) == 1 and isinstance(redirects[0], hc._NoRedirect)


@pytest.mark.parametrize("failure", [
    urllib.error.URLError("refused"),
    ConnectionResetError(),
    http.client.BadStatusLine("junk"),
    TimeoutError(),
])
def test_probe_false_on_errors(monkeypatch, failure):
    opener = MagicMock()
    opener.open.side_effect = failure
    monkeypatch.setattr(hc, "build_opener", lambda _ctx: opener)
    assert hc.probe("https://127.0.0.1:1/api/health/") is False
    assert opener.open.call_args.kwargs["timeout"] == hc.TIMEOUT_SECONDS


def test_main_exit_codes(ini, monkeypatch, capsys):
    monkeypatch.setenv("SETHLANS_MANAGER_PORT", "9443")
    urls = []
    monkeypatch.setattr(hc, "probe", lambda url: urls.append(url) or True)
    assert hc.main() == 0
    assert urls == ["https://127.0.0.1:9443/api/health/"]

    monkeypatch.setattr(hc, "probe", lambda url: False)
    assert hc.main() == 1
    assert "9443" in capsys.readouterr().err


def test_main_bad_port_is_unhealthy(ini, monkeypatch, capsys):
    monkeypatch.setenv("SETHLANS_MANAGER_PORT", "eighty")
    assert hc.main() == 1
    assert "eighty" in capsys.readouterr().err
