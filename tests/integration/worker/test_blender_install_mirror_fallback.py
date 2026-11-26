# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A worker installs Blender from a mirror when the official site refuses it (#320, BS-REL-1, BS-DL-3).

Real parts: ``blender_install.install``, ``shared.blender_sources`` (source
list, release list, cache, download), ``requests`` over real TLS, archive
extraction and the ``ToolManager`` scan. Two HTTPS servers run on loopback
with a certificate from a test CA that ``requests`` trusts through
``REQUESTS_CA_BUNDLE``: "official" answers 403 to everything, "mirror"
serves the release pages, the checksum file and a small ``.tar.xz`` made
here.

Outside the parts, so faked: the machine's platform id is pinned to
``linux-x64`` so the build is the same ``.tar.xz`` on every OS; the fake
Blender inside it sits where ``platform_utils`` looks on this OS.
"""
import contextlib
import datetime
import hashlib
import http.server
import io
import ipaddress
import json
import logging
import ssl
import tarfile
import threading
from pathlib import Path, PurePath

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from shared.blender_sources import SOURCES_ENV, sources
from sethlans_worker_agent import blender_install, config
from sethlans_worker_agent import tool_manager as tool_manager_module
from sethlans_worker_agent.platform_utils import get_executable_path_for_blender

VERSION = '4.5.8'
PLATFORM_ID = 'linux-x64'
BUILD = f'blender-{VERSION}-{PLATFORM_ID}.tar.xz'
INSTALL_DIR = f'blender-{VERSION}-{PLATFORM_ID}'
JOIN_TIMEOUT = 5


def _name(common_name):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _pem(path, data):
    path.write_bytes(data)
    return path


def _key_usage(**on):
    names = ('digital_signature', 'content_commitment', 'key_encipherment', 'data_encipherment', 'key_agreement',
             'key_cert_sign', 'crl_sign', 'encipher_only', 'decipher_only')
    return x509.KeyUsage(**{name: on.get(name, False) for name in names})


def _test_ca(folder):
    """``(ca_pem, cert_pem, key_pem)``: a test CA and a 127.0.0.1 server certificate it signed."""
    now = datetime.datetime.now(datetime.timezone.utc)
    valid = dict(not_valid_before=now - datetime.timedelta(days=1), not_valid_after=now + datetime.timedelta(days=1))
    ca_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca = (x509.CertificateBuilder(subject_name=_name('Sethlans test CA'), issuer_name=_name('Sethlans test CA'),
                                  public_key=ca_key.public_key(), serial_number=x509.random_serial_number(), **valid)
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(_key_usage(key_cert_sign=True, crl_sign=True), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    leaf = (x509.CertificateBuilder(subject_name=_name('127.0.0.1'), issuer_name=ca.subject,
                                    public_key=key.public_key(), serial_number=x509.random_serial_number(), **valid)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(_key_usage(digital_signature=True), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),
                           critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    return (_pem(folder / 'ca.pem', ca.public_bytes(pem)), _pem(folder / 'server.pem', leaf.public_bytes(pem)),
            _pem(folder / 'server.key', key.private_bytes(pem, serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption())))


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves ``server.routes`` (path -> bytes); any other path gets ``server.other_status``."""

    def do_GET(self):  # noqa: N802 - http.server API
        self.server.hits.append(self.path)
        body = self.server.routes.get(self.path)
        status = 200 if body is not None else self.server.other_status
        body = body if body is not None else b'refused'
        self.send_response(status)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@contextlib.contextmanager
def _https_server(cert_pem, key_pem, routes, other_status):
    """A running loopback HTTPS server; yields it (``.hits`` lists the paths asked for)."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_pem, key_pem)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    server.routes, server.other_status, server.hits = routes, other_status, []
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(JOIN_TIMEOUT)


def _build_archive():
    """A ``.tar.xz`` holding a fake Blender where ``platform_utils`` looks for it on this OS."""
    root = PurePath('root')
    executable = PurePath(get_executable_path_for_blender(root, INSTALL_DIR)).relative_to(root)
    content = b'fake blender'
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:xz') as tar:
        member = tarfile.TarInfo(executable.as_posix())
        member.size, member.mode = len(content), 0o755
        tar.addfile(member, io.BytesIO(content))
    return buffer.getvalue()


def _mirror_routes(prefix, archive):
    series = f'{prefix}Blender4.5/'
    return {
        prefix: b'<html><a href="Blender4.5/">Blender4.5/</a></html>',
        series: (f'<html><a href="{BUILD}">{BUILD}</a> '
                 f'<a href="blender-{VERSION}.sha256">blender-{VERSION}.sha256</a></html>').encode(),
        f'{series}blender-{VERSION}.sha256': f'{hashlib.sha256(archive).hexdigest()}  {BUILD}\n'.encode(),
        f'{series}{BUILD}': archive,
    }


def test_install_falls_back_to_mirror_when_official_answers_403(tool_manager, monkeypatch, tmp_path, caplog):
    """#320, BS-REL-1, BS-DL-3: the official 403 is logged and the build installs from the mirror.

    Also guards that an install drops the ToolManager scan cache, so the
    new version is reported at once (P2-F5).
    """
    ca_pem, cert_pem, key_pem = _test_ca(tmp_path)
    archive = _build_archive()
    cache_file = tmp_path / 'blender_versions_cache.json'
    monkeypatch.setattr(blender_install, 'tool_manager_instance', tool_manager)
    monkeypatch.setattr(config, 'BLENDER_VERSIONS_CACHE_FILE', cache_file)
    monkeypatch.setattr(blender_install, 'current_platform_id', lambda: PLATFORM_ID)
    monkeypatch.setattr(tool_manager_module, 'current_platform_id', lambda: PLATFORM_ID)
    monkeypatch.setattr(sources, '_last_worked', None)
    monkeypatch.setenv('REQUESTS_CA_BUNDLE', str(ca_pem))
    monkeypatch.setenv('NO_PROXY', '127.0.0.1')
    tool_manager.scan_for_local_blenders()  # a fresh, empty scan cache

    with _https_server(cert_pem, key_pem, {}, 403) as official, \
            _https_server(cert_pem, key_pem, _mirror_routes('/blender/release/', archive), 404) as mirror:
        official_host = f'127.0.0.1:{official.server_address[1]}'
        mirror_url = f'https://127.0.0.1:{mirror.server_address[1]}/blender/release/'
        monkeypatch.setenv(SOURCES_ENV, f'https://{official_host}/release/,{mirror_url}')
        with caplog.at_level(logging.INFO, logger='shared.blender_sources'):
            result = blender_install.install(VERSION, blocking=True)

    assert result.ok, result.error
    assert Path(result.exe_path).read_bytes() == b'fake blender'
    assert Path(result.exe_path).is_relative_to(tool_manager.blender_dir / INSTALL_DIR)
    assert official.hits and f'/blender/release/Blender4.5/{BUILD}' in mirror.hits
    refused = [r for r in caplog.records
               if r.levelno == logging.WARNING and official_host in r.getMessage() and 'HTTP 403' in r.getMessage()]
    assert refused, [r.getMessage() for r in caplog.records]
    assert json.loads(cache_file.read_text())[VERSION][PLATFORM_ID]['source'] == mirror_url
    assert sorted(p.name for p in tool_manager.blender_dir.iterdir()) == [INSTALL_DIR]  # no archive, no .part
    assert [b['version'] for b in tool_manager.scan_for_local_blenders()] == [VERSION]
