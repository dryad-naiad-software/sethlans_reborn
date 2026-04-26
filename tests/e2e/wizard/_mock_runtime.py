# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Standalone mock-runtime script for the wizard E2E tests.

Spawned by the test driver in place of the real Django manager / worker.
Two modes:

* ``--mode=health-ok`` (default) — bind an HTTPS listener on the given
  port and respond 200 + ``{"boot_id": ..., "worker_id": ..., "version": ...}``
  to ``GET /api/health/``. Satisfies the wizard's FR-W14 probe so
  ``/runtime-ready/`` flips to ``ready``.
* ``--mode=fail-immediately`` — exit non-zero immediately. The launcher's
  port-bind watcher (FR-L7b) will time out and write a
  ``.runtime_failed`` marker.

Self-signed cert is generated on the fly via ``cryptography`` (pinned in
manager/requirements.txt and so available in the test environment).
The cert is written to a temp file and removed on exit.

Stdlib + cryptography only.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import ssl
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

logger = logging.getLogger("mock_runtime")

# Hardcoded payload — the wizard's probe just checks for the three keys.
_BOOT_ID = uuid.uuid4().hex
_WORKER_ID = "mock-runtime"
_VERSION = "0.0.0-test"


def _generate_self_signed_cert(cert_path: Path, key_path: Path) -> None:
    """Generate a 1-day RSA-2048 self-signed cert + key at the given paths."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "mock-runtime")]
    )
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.DNSName("127.0.0.1")]
            ),
            critical=False,
        )
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


class _HealthHandler(BaseHTTPRequestHandler):
    """Serve ``GET /api/health/`` with the three FR-W14 keys."""

    # Silence noisy default logging on every request.
    def log_message(self, format, *args):  # noqa: A002 — stdlib signature
        return

    def do_GET(self):  # noqa: N802 — stdlib name
        if self.path.rstrip("/") in ("/api/health", ""):
            body = json.dumps(
                {
                    "boot_id": _BOOT_ID,
                    "worker_id": _WORKER_ID,
                    "version": _VERSION,
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


def _serve_https(port: int, stop_event: threading.Event) -> int:
    """Run the HTTPS health server until *stop_event* is set."""
    tmpdir = Path(tempfile.mkdtemp(prefix="mock-runtime-cert-"))
    cert_path = tmpdir / "tls.crt"
    key_path = tmpdir / "tls.key"
    _generate_self_signed_cert(cert_path, key_path)
    try:
        server = HTTPServer(("127.0.0.1", port), _HealthHandler)
    except OSError as exc:
        # Port already in use, etc. — bail loudly so the test fails
        # with a meaningful traceback instead of a bind hang.
        print(f"mock_runtime: bind error on 127.0.0.1:{port}: {exc}",
              file=sys.stderr, flush=True)
        return 2
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    print(f"mock_runtime: bound HTTPS on 127.0.0.1:{port}", flush=True)
    server.timeout = 0.25
    try:
        while not stop_event.is_set():
            server.handle_request()
    finally:
        try:
            server.server_close()
        except OSError:
            pass
        for p in (cert_path, key_path):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
        try:
            tmpdir.rmdir()
        except OSError:
            pass
    return 0


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _handler(signum, frame):  # noqa: ARG001
        stop_event.set()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description="Wizard E2E mock runtime.")
    parser.add_argument(
        "--mode", choices=("health-ok", "fail-immediately"),
        default="health-ok",
    )
    parser.add_argument(
        "--port", type=int, default=8080,
        help="Port to bind HTTPS server on.",
    )
    args = parser.parse_args(argv)

    if args.mode == "fail-immediately":
        print("mock_runtime: fail-immediately mode; exiting 1", flush=True)
        return 1

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)
    rc = _serve_https(args.port, stop_event)
    # Ensure the parent SIGTERM is fully drained before exit.
    time.sleep(0.05)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)


# Helper for tests / linter to verify env references.
_ = os.environ
