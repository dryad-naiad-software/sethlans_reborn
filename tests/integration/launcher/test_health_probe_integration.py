# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Integration tests for ``launcher.health_probe.wait_for_health``.

Spins up a real ``ThreadingHTTPServer`` on an ephemeral loopback port
with a self-signed TLS context, configures per-request behavior, and
drives ``wait_for_health`` against it on real sockets to verify the
True/False contract that the unit tests exercise via mocks.

Covers FR-3 / FR-5 / FR-6 / FR-W14 envelope semantics on the wire.
"""

from __future__ import annotations

import datetime
import json
import socket
import ssl
import tempfile
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from launcher.health_probe import wait_for_health

# cryptography ships with the project; build a fresh self-signed cert
# for each test server so we don't smuggle a real cert path through.
pytest.importorskip("cryptography")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402


# ---------------------------------------------------------------------
# Helpers — self-signed TLS server
# ---------------------------------------------------------------------

def _make_self_signed(cert_path: Path, key_path: Path) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(x509.NameOID.COMMON_NAME, "localhost"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Handler(BaseHTTPRequestHandler):
    # Inject behavior via class attribute — fresh subclass per server.
    behavior = "ok"

    def do_GET(self):
        if self.path != "/api/health/":
            self.send_response(404)
            self.end_headers()
            return
        if self.behavior == "ok":
            body = json.dumps(
                {"boot_id": "abc", "version": "1.0"},
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.behavior == "missing_envelope":
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.behavior == "five_hundred":
            self.send_response(500)
            self.end_headers()
        elif self.behavior == "hang":
            time.sleep(10.0)

    def log_message(self, *_a, **_k):
        # Silence access logs in test output.
        return


@contextmanager
def _https_server(behavior: str):
    """Yield (port, server_thread) for a TLS server on 127.0.0.1."""
    with tempfile.TemporaryDirectory() as tmp:
        cert_path = Path(tmp) / "cert.pem"
        key_path = Path(tmp) / "key.pem"
        _make_self_signed(cert_path, key_path)

        # Subclass per-test so behavior assignment is scoped.
        handler_cls = type("_TestHandler", (_Handler,), {"behavior": behavior})
        port = _free_port()
        server = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert_path), str(key_path))
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield port
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2.0)


# ---------------------------------------------------------------------
# Single-URL integration
# ---------------------------------------------------------------------

class TestSingleURL:

    def test_returns_true_against_live_envelope_server(self):
        with _https_server("ok") as port:
            url = f"https://127.0.0.1:{port}/api/health/"
            assert wait_for_health(url, timeout=5.0) is True

    def test_returns_false_when_envelope_missing(self):
        with _https_server("missing_envelope") as port:
            url = f"https://127.0.0.1:{port}/api/health/"
            assert wait_for_health(url, timeout=2.0) is False

    def test_returns_false_on_500(self):
        with _https_server("five_hundred") as port:
            url = f"https://127.0.0.1:{port}/api/health/"
            assert wait_for_health(url, timeout=2.0) is False


# ---------------------------------------------------------------------
# Two-URL serial polling (FR-6)
# ---------------------------------------------------------------------

class TestTwoURLSerial:

    def test_both_servers_healthy_against_real_sockets(self):
        with _https_server("ok") as p_mgr, _https_server("ok") as p_wkr:
            mgr = f"https://127.0.0.1:{p_mgr}/api/health/"
            wkr = f"https://127.0.0.1:{p_wkr}/api/health/"
            deadline = time.monotonic() + 30.0
            assert wait_for_health(
                mgr, timeout=deadline - time.monotonic(),
            ) is True
            assert wait_for_health(
                wkr, timeout=deadline - time.monotonic(),
            ) is True

    def test_first_succeeds_second_500(self):
        # Manager succeeds, worker returns 500 -> FR-6 path returns
        # False on the second wait_for_health.
        with _https_server("ok") as p_mgr, _https_server("five_hundred") as p_wkr:
            mgr = f"https://127.0.0.1:{p_mgr}/api/health/"
            wkr = f"https://127.0.0.1:{p_wkr}/api/health/"
            deadline = time.monotonic() + 30.0
            assert wait_for_health(
                mgr, timeout=deadline - time.monotonic(),
            ) is True
            # Bound the worker timeout so the test does not wait the
            # full 30 s budget — the 500 response is returned per poll
            # so this trips on the deadline check.
            assert wait_for_health(
                wkr, timeout=2.0,
            ) is False
