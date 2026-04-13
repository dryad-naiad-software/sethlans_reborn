# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests for worker TLS startup (spec FR-4 through FR-14).

Verifies the worker generates a cert, starts uvicorn with TLS, and
serves HTTPS requests. Tests cover auto-generation, existing cert
reuse, BYO cert, TLS version floor, startup log message, and HTTP
fallback rejection.
"""

import json
import logging
import socket
import ssl
import time
import urllib.error
import urllib.request

import pytest

from sethlans_worker_agent import config
from sethlans_worker_agent.web_ui import auth, server
from shared.cert_utils import (
    generate_self_signed_cert,
    get_cert_fingerprint,
    load_and_validate_cert,
)

logger = logging.getLogger(__name__)

_NO_VERIFY_CTX = ssl.create_default_context()
_NO_VERIFY_CTX.check_hostname = False
_NO_VERIFY_CTX.verify_mode = ssl.CERT_NONE


def _find_free_port():
    """Find and return a free TCP port on 127.0.0.1."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_for_server(port, attempts=20, delay=0.25):
    """Poll until the HTTPS server is accepting connections."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for _ in range(attempts):
        try:
            urllib.request.urlopen(
                f'https://127.0.0.1:{port}/api/status',
                timeout=1, context=ctx,
            )
            return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(delay)
    return False


def _mock_hardware(mocker):
    """Mock hardware detection to avoid Blender dependency."""
    mocker.patch(
        'sethlans_worker_agent.web_ui.status.get_gpu_device_details',
        return_value=[],
    )
    mocker.patch(
        'sethlans_worker_agent.web_ui.status.get_cpu_thread_count',
        return_value=8,
    )
    mocker.patch(
        'sethlans_worker_agent.web_ui.status.tool_manager_instance'
        '.scan_for_local_blenders',
        return_value=[],
    )
    mocker.patch(
        'sethlans_worker_agent.web_ui.status.system_monitor.WORKER_ID',
        42,
    )


@pytest.fixture()
def tls_server(mocker, tmp_path):
    """Start a real uvicorn HTTPS server with auto-generated cert.

    Yields a dict with base_url, cert_path, key_path, and port.
    Stops the server on teardown.
    """
    mocker.patch.object(config, 'UI_ENABLED', True)
    mocker.patch.object(config, 'UI_BIND_ADDRESS', '127.0.0.1')
    auth.reset_cache()
    mocker.patch.object(config, 'config_file_path', tmp_path / 'config.ini')
    auth.set_password('test-tls-pw')

    tls_dir = tmp_path / 'tls'
    cert_path = tls_dir / 'cert.pem'
    key_path = tls_dir / 'key.pem'
    generate_self_signed_cert(cert_path, key_path)

    port = _find_free_port()
    mocker.patch.object(config, 'UI_PORT', port)
    _mock_hardware(mocker)

    server.start_server(cert_path, key_path)
    assert _wait_for_server(port), "Server did not start in time"

    yield {
        'base_url': f'https://127.0.0.1:{port}',
        'port': port,
        'cert_path': cert_path,
        'key_path': key_path,
    }

    server.stop_server()
    auth.reset_cache()


class TestWorkerTlsAutoGeneration:
    """FR-4: Worker auto-generates cert on first run."""

    def test_auto_generated_cert_serves_https(self, tls_server):
        """GET /api/status over HTTPS returns valid JSON."""
        url = f"{tls_server['base_url']}/api/status"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(
            req, timeout=5, context=_NO_VERIFY_CTX,
        ) as resp:
            assert resp.status == 200
            body = json.loads(resp.read().decode())
            assert 'worker' in body
            assert 'hardware' in body
            assert 'config' in body

    def test_cert_files_exist_after_generation(self, tls_server):
        """cert.pem and key.pem files exist after auto-generation."""
        assert tls_server['cert_path'].exists()
        assert tls_server['key_path'].exists()

    def test_cert_fingerprint_is_valid_hex(self, tls_server):
        """The generated cert has a valid 64-char hex fingerprint."""
        cert = load_and_validate_cert(
            tls_server['cert_path'], tls_server['key_path'],
        )
        fp = get_cert_fingerprint(cert)
        assert len(fp) == 64
        assert all(c in '0123456789abcdef' for c in fp)


class TestWorkerTlsExistingCert:
    """FR-5: Worker loads existing cert without regenerating."""

    def test_existing_cert_not_overwritten(self, mocker, tmp_path):
        """setup_certificates loads existing cert, does not regenerate."""
        from sethlans_worker_agent import tls_setup

        tls_dir = tmp_path / 'tls'
        cert_path = tls_dir / 'cert.pem'
        key_path = tls_dir / 'key.pem'

        generate_self_signed_cert(cert_path, key_path)
        original_bytes = cert_path.read_bytes()

        mocker.patch.object(config, 'TLS_CERT_FILE', '')
        mocker.patch.object(config, 'TLS_KEY_FILE', '')
        mocker.patch(
            'sethlans_worker_agent.tls_setup.get_tls_dir',
            return_value=tls_dir,
        )

        _, _, fp = tls_setup.setup_certificates()
        assert cert_path.read_bytes() == original_bytes
        assert len(fp) == 64


class TestWorkerTlsByoCert:
    """FR-8, FR-9: BYO cert loaded when configured."""

    def test_byo_cert_used_for_server(self, mocker, tmp_path):
        """BYO cert/key are used when both are configured."""
        from sethlans_worker_agent import tls_setup

        byo_dir = tmp_path / 'byo'
        byo_cert = byo_dir / 'cert.pem'
        byo_key = byo_dir / 'key.pem'
        generate_self_signed_cert(byo_cert, byo_key)

        mocker.patch.object(config, 'TLS_CERT_FILE', str(byo_cert))
        mocker.patch.object(config, 'TLS_KEY_FILE', str(byo_key))

        cert_path, key_path, fp = tls_setup.setup_certificates()
        assert cert_path == byo_cert
        assert key_path == byo_key
        assert len(fp) == 64

    def test_partial_byo_falls_back(self, mocker, tmp_path, caplog):
        """Only cert_file set (no key_file) → fallback to auto-gen."""
        from sethlans_worker_agent import tls_setup

        tls_dir = tmp_path / 'tls'
        mocker.patch.object(config, 'TLS_CERT_FILE', '/some/cert.pem')
        mocker.patch.object(config, 'TLS_KEY_FILE', '')
        mocker.patch(
            'sethlans_worker_agent.tls_setup.get_tls_dir',
            return_value=tls_dir,
        )

        with caplog.at_level(logging.WARNING):
            _, _, fp = tls_setup.setup_certificates()
        assert len(fp) == 64
        assert any(
            'Both must be provided' in r.message
            for r in caplog.records
        )


class TestWorkerTlsVersionFloor:
    """NF-1: SSL context enforces TLS 1.2 minimum."""

    def test_ssl_context_has_tls_12_minimum(self, tls_server):
        """The built SSLContext enforces TLS 1.2 as minimum version."""
        from shared.tls_utils import build_ssl_context
        ctx = build_ssl_context(
            tls_server['cert_path'], tls_server['key_path'],
        )
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2


class TestWorkerTlsStartupLog:
    """FR-14: Startup log includes ``https://`` URL."""

    def test_startup_log_contains_https(self, mocker, tmp_path, caplog):
        """start_server() logs the HTTPS URL."""
        mocker.patch.object(config, 'UI_ENABLED', True)
        mocker.patch.object(config, 'UI_BIND_ADDRESS', '127.0.0.1')
        auth.reset_cache()
        mocker.patch.object(
            config, 'config_file_path', tmp_path / 'config.ini',
        )
        auth.set_password('test-log-pw')
        _mock_hardware(mocker)

        tls_dir = tmp_path / 'tls'
        cert_path = tls_dir / 'cert.pem'
        key_path = tls_dir / 'key.pem'
        generate_self_signed_cert(cert_path, key_path)

        port = _find_free_port()
        mocker.patch.object(config, 'UI_PORT', port)

        with caplog.at_level(logging.INFO):
            server.start_server(cert_path, key_path)
            _wait_for_server(port)

        try:
            assert any(
                'Worker Web UI started on https://' in r.message
                for r in caplog.records
            ), "Expected HTTPS startup log message not found"
        finally:
            server.stop_server()
            auth.reset_cache()


class TestWorkerHttpFallbackRejected:
    """AC: HTTP request to HTTPS port fails."""

    def test_http_request_to_https_port_fails(self, tls_server):
        """Plain HTTP request to the HTTPS port does not succeed."""
        port = tls_server['port']
        with pytest.raises(
            (urllib.error.URLError, ConnectionError, OSError),
        ):
            urllib.request.urlopen(
                f'http://127.0.0.1:{port}/api/status',
                timeout=3,
            )
