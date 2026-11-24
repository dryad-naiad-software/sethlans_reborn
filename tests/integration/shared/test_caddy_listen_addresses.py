# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Real ``caddy adapt`` listen addresses for every Caddyfile we ship
(issue #309).

The host in a Caddyfile site address is only a Host matcher, so a
site written ``http://127.0.0.1:PORT`` listens on ``:PORT`` (every
interface) unless it carries ``bind``. These tests run the pinned
Caddy binary's ``adapt`` command on the rendered manager and worker
templates and on the static Docker Caddyfiles, then assert on the
JSON Caddy would run:

* the loopback plaintext server listens on ``127.0.0.1:<port>`` only;
* the public TLS server listens on ``:<port>`` (all interfaces).

Skips cleanly when no Caddy binary is in ``.venv-build/caddy/``
(``python tools/fetch_caddy.py --target-dir .venv-build/caddy``), and
skips the Docker cases where ``deploy/`` is absent (the Docker test
image).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from sethlans_manager.caddy_template import render_manager_caddyfile
from sethlans_worker_agent.caddy_template import render_worker_caddyfile
from shared.frozen_paths import get_caddy_path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKER_DIR = REPO_ROOT / "deploy" / "docker"
ADAPT_TIMEOUT_S = 30

PUBLIC_PORT = 8443
LOOPBACK_PORT = 18444


@pytest.fixture
def caddy_binary():
    """Skip if a Caddy binary is not available in this checkout."""
    path = Path(get_caddy_path())
    if not path.is_file():
        pytest.skip(f"Caddy binary not found at {path}")
    return path


def docker_caddyfile(component: str) -> Path:
    """Return a static Docker Caddyfile path, or skip this case.

    ``deploy/`` is not copied into the Docker test image
    (``Dockerfile.test``), so these cases skip there; the template
    cases still run.
    """
    path = DOCKER_DIR / component / "Caddyfile"
    if not path.is_file():
        pytest.skip(f"{path} not present (deploy/ is not in the Docker test image)")
    return path


@pytest.fixture
def data_dir(tmp_path):
    tls = tmp_path / "tls"
    tls.mkdir()
    (tls / "cert.pem").write_text("CERT")
    (tls / "key.pem").write_text("KEY")
    return tmp_path


def adapt_listens(caddy: Path, caddyfile: Path, env: dict | None = None):
    """Run ``caddy adapt`` and return the sorted listen lists."""
    proc = subprocess.run(
        [str(caddy), "adapt", "--config", str(caddyfile),
         "--adapter", "caddyfile"],
        capture_output=True, text=True, timeout=ADAPT_TIMEOUT_S,
        env={**os.environ, **(env or {})},
    )
    assert proc.returncode == 0, proc.stderr
    servers = json.loads(proc.stdout)["apps"]["http"]["servers"]
    return sorted(server["listen"] for server in servers.values())


def assert_bound(listens):
    assert listens == sorted([
        [f":{PUBLIC_PORT}"],
        [f"127.0.0.1:{LOOPBACK_PORT}"],
    ]), listens


def test_manager_template_listen_addresses(caddy_binary, data_dir, tmp_path):
    caddyfile = tmp_path / "manager.Caddyfile"
    caddyfile.write_text(render_manager_caddyfile(
        public_tls_port=PUBLIC_PORT, loopback_plaintext_port=LOOPBACK_PORT,
        waitress_public_port=18090, waitress_internal_port=18088,
        cert_path=data_dir / "tls" / "cert.pem",
        key_path=data_dir / "tls" / "key.pem",
        manager_data_dir=data_dir,
    ), encoding="utf-8")
    assert_bound(adapt_listens(caddy_binary, caddyfile))


def test_worker_template_listen_addresses(caddy_binary, data_dir, tmp_path):
    caddyfile = tmp_path / "worker.Caddyfile"
    caddyfile.write_text(render_worker_caddyfile(
        public_tls_port=PUBLIC_PORT, loopback_plaintext_port=LOOPBACK_PORT,
        waitress_upstream_port=18091,
        cert_path=data_dir / "tls" / "cert.pem",
        key_path=data_dir / "tls" / "key.pem",
        worker_data_dir=data_dir,
    ), encoding="utf-8")
    assert_bound(adapt_listens(caddy_binary, caddyfile))


def test_docker_manager_caddyfile_listen_addresses(caddy_binary, data_dir):
    env = {
        "SETHLANS_MANAGER_CADDY_PUBLIC_TLS_PORT": str(PUBLIC_PORT),
        "SETHLANS_MANAGER_CADDY_LOOPBACK_PORT": str(LOOPBACK_PORT),
        "SETHLANS_MANAGER_WAITRESS_PUBLIC_PORT": "18090",
        "SETHLANS_MANAGER_WAITRESS_LOOPBACK_PORT": "18088",
        "SETHLANS_MANAGER_CERT_PATH": str(data_dir / "tls" / "cert.pem"),
        "SETHLANS_MANAGER_KEY_PATH": str(data_dir / "tls" / "key.pem"),
    }
    assert_bound(adapt_listens(
        caddy_binary, docker_caddyfile("manager"), env))


def test_docker_worker_caddyfile_listen_addresses(caddy_binary, data_dir):
    env = {
        "SETHLANS_WORKER_CADDY_PUBLIC_TLS_PORT": str(PUBLIC_PORT),
        "SETHLANS_WORKER_CADDY_LOOPBACK_PORT": str(LOOPBACK_PORT),
        "SETHLANS_WORKER_WAITRESS_UPSTREAM_PORT": "18091",
        "SETHLANS_WORKER_CERT_PATH": str(data_dir / "tls" / "cert.pem"),
        "SETHLANS_WORKER_KEY_PATH": str(data_dir / "tls" / "key.pem"),
    }
    assert_bound(adapt_listens(
        caddy_binary, docker_caddyfile("worker"), env))
