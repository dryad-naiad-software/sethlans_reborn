# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
A worker installs Blender from its manager's cache with no online request (Blender sources BS-WK-4..7).

Real parts: the worker's ``blender_install.install`` and ``blender_from_manager``
over a real certificate-pinned ``requests`` session (``tls_adapter``'s
``PinningHTTPAdapter``), the manager's routes, token check and Blender build
views in a live server over TLS on loopback, the test database, archive
extraction and the ``ToolManager`` scan.

Outside the parts, so faked: the worker's saved enrollment (token, manager
URL, pinned fingerprint) is set on ``config`` and ``tls_adapter.get_session``
directly; the machine's platform id is pinned to ``linux-x64`` so the build
is the same ``.tar.xz`` on every OS. The online sources point at a host that
cannot resolve, and the online step is spied on: it must never run.
"""
import hashlib
import logging
import ssl

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from django.contrib.auth import get_user_model
from django.db import connections
from django.test.testcases import LiveServerThread, _StaticFilesHandler
from rest_framework.authtoken.models import Token

from shared.blender_sources import SOURCES_ENV, sources
from sethlans_worker_agent import blender_install, config, tls_adapter
from sethlans_worker_agent import tool_manager as tool_manager_module
from workers.constants import BlenderBuildStatus
from workers.models import BlenderBuild, Worker

from .test_blender_install_mirror_fallback import BUILD, INSTALL_DIR, PLATFORM_ID, VERSION, _build_archive, _test_ca

READY_TIMEOUT = 10


class _TlsLiveServerThread(LiveServerThread):
    """Django's live server thread with its listening socket wrapped in TLS."""

    ssl_context = None

    def _create_server(self, connections_override=None):
        server = super()._create_server(connections_override=connections_override)
        server.socket = self.ssl_context.wrap_socket(server.socket, server_side=True)
        return server


@pytest.fixture
def tls_manager(transactional_db, tmp_path):
    """``(base_url, fingerprint)`` of a live manager over TLS on 127.0.0.1 (pytest-django's connection sharing)."""
    _ca, cert_pem, key_pem = _test_ca(tmp_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_pem, key_pem)
    fingerprint = x509.load_pem_x509_certificate(cert_pem.read_bytes()).fingerprint(hashes.SHA256()).hex()
    shared = {alias: conn for alias, conn in ((a, connections[a]) for a in connections)
              if conn.vendor == 'sqlite' and conn.is_in_memory_db()}
    for conn in shared.values():
        conn.inc_thread_sharing()
    thread = _TlsLiveServerThread('127.0.0.1', _StaticFilesHandler, connections_override=shared)
    thread.ssl_context = context
    thread.daemon = True
    thread.start()
    try:
        assert thread.is_ready.wait(READY_TIMEOUT) and thread.error is None, thread.error
        yield f'https://127.0.0.1:{thread.port}/api/', fingerprint
    finally:
        thread.terminate()
        for conn in shared.values():
            conn.dec_thread_sharing()


@pytest.fixture
def pinned_worker(tls_manager, monkeypatch):
    """An enrolled worker: its token, the manager URL and a session pinned to the manager's certificate."""
    base_url, fingerprint = tls_manager
    user = get_user_model().objects.create_user(username='worker_from_manager')
    Worker.objects.create(hostname='from-manager-host', user=user, is_active=True, blender_platform=PLATFORM_ID)
    session = tls_adapter._new_session(fingerprint)
    monkeypatch.setattr(config, 'API_TOKEN', Token.objects.create(user=user).key)
    monkeypatch.setattr(config, 'MANAGER_API_URL', base_url)
    monkeypatch.setattr(tls_adapter, 'get_session', lambda: session)
    yield
    session.close()


def test_worker_installs_from_ready_manager_with_no_online_request(
        pinned_worker, tool_manager, settings, monkeypatch, mocker, tmp_path, caplog):
    """BS-WK-4..7, BS-MGR-10, BS-MGR-11: manager READY + online sources unreachable: installed from the manager."""
    archive = _build_archive()
    settings.BLENDER_CACHE_ROOT = tmp_path / 'manager-blender'
    (settings.BLENDER_CACHE_ROOT / VERSION).mkdir(parents=True)
    (settings.BLENDER_CACHE_ROOT / VERSION / BUILD).write_bytes(archive)
    BlenderBuild.objects.create(version=VERSION, platform=PLATFORM_ID, file_name=BUILD, status=BlenderBuildStatus.READY,
                                sha256=hashlib.sha256(archive).hexdigest(), size=len(archive))
    monkeypatch.setattr(blender_install, 'tool_manager_instance', tool_manager)
    monkeypatch.setattr(blender_install, 'current_platform_id', lambda: PLATFORM_ID)
    monkeypatch.setattr(tool_manager_module, 'current_platform_id', lambda: PLATFORM_ID)
    monkeypatch.setattr(config, 'BLENDER_VERSIONS_CACHE_FILE', tmp_path / 'blender_versions_cache.json')
    monkeypatch.setattr(sources, '_last_worked', None)
    monkeypatch.setenv(SOURCES_ENV, 'https://download.blender.invalid/release/')
    monkeypatch.setenv('NO_PROXY', '127.0.0.1')
    find_build, download_build = mocker.spy(blender_install, 'find_build'), mocker.spy(blender_install, 'download_build')

    with caplog.at_level(logging.INFO, logger='sethlans_worker_agent.blender_from_manager'):
        result = blender_install.install(VERSION, blocking=True, wait_for_manager=True)

    assert result.ok, result.error
    assert result.manager_state == 'ready'
    assert (tool_manager.blender_dir / INSTALL_DIR).is_dir()
    assert sorted(p.name for p in tool_manager.blender_dir.iterdir()) == [INSTALL_DIR]  # no archive, no .part
    assert find_build.call_count == download_build.call_count == 0  # BS-WK-7: no online request at all
    assert any('downloaded from the manager 127.0.0.1:' in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]
