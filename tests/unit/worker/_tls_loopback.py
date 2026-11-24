# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A real HTTPS server on loopback for the TLS pinning tests (#306).

``serving(folder)`` makes a self-signed EC certificate in ``folder``, serves
it on ``127.0.0.1`` (an ephemeral port) and yields a ``Loopback``: the URL,
the certificate's SHA-256 fingerprint and the requests that reached it. GET
and POST both answer ``200 ok``. ``count_insecure`` makes requests and counts
the ``InsecureRequestWarning`` they raise; the warning means an unpinned
request (urllib3 counts a pinned connection as verified).

Module name starts with an underscore so pytest does not collect it.
"""
import contextlib
import dataclasses
import datetime
import hashlib
import http.server
import ssl
import threading
import warnings

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from urllib3.exceptions import InsecureRequestWarning

TIMEOUT = 5


@dataclasses.dataclass
class Loopback:
    url: str
    fingerprint: str
    hits: list  # the request methods that reached the server, in order


def _self_signed(folder):
    """``(cert_path, key_path, sha256 hex of the DER certificate)`` for a fresh EC certificate."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = folder / 'cert.pem', folder / 'key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cert_path, key_path, hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


class _Handler(http.server.BaseHTTPRequestHandler):
    def _answer(self):
        self.server.hits.append(self.command)
        length = int(self.headers.get('Content-Length') or 0)
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.write(b'ok')

    do_GET = _answer  # noqa: N815 - http.server API
    do_POST = _answer  # noqa: N815 - http.server API

    def log_message(self, *args):
        pass


class _Server(http.server.ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # a client that drops the connection after a failed pin is expected here


@contextlib.contextmanager
def serving(folder):
    """A running server; ``OSError`` when loopback cannot be bound. Shut down and closed on exit."""
    cert_path, key_path, fingerprint = _self_signed(folder)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server = _Server(('127.0.0.1', 0), _Handler)
    server.hits = []
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    try:
        yield Loopback(f'https://127.0.0.1:{server.server_address[1]}/', fingerprint, server.hits)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(TIMEOUT)


def count_insecure(send, n=1):
    """Call ``send()`` ``n`` times (each must answer 200 ok); the ``InsecureRequestWarning`` count."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        for _ in range(n):
            response = send()
            assert (response.status_code, response.content) == (200, b'ok')
    return sum(1 for warning in caught if issubclass(warning.category, InsecureRequestWarning))
