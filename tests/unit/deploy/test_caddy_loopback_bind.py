# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The loopback plaintext site binds 127.0.0.1 only (issue #309).

In a Caddyfile the host in a site address (``http://127.0.0.1:PORT``)
is only a Host matcher; Caddy still listens on every interface unless
the site carries ``bind``. These tests check that the loopback site
of each Caddyfile we ship starts with ``bind 127.0.0.1`` and that the
public TLS site has no ``bind`` (it must stay reachable on the LAN).
Each Caddyfile must hold exactly those two sites; any other site
fails the test rather than going unchecked.

Covers the manager and worker templates and the two static Docker
Caddyfiles (the Docker cases skip where ``deploy/`` is absent, as in
the Docker test image). ``caddy adapt`` evidence for the same rule lives in
``tests/integration/shared/test_caddy_listen_addresses.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sethlans_manager.caddy_template import render_manager_caddyfile
from sethlans_worker_agent.caddy_template import render_worker_caddyfile

REPO_ROOT = Path(__file__).resolve().parents[3]
DOCKER_DIR = REPO_ROOT / "deploy" / "docker"
LOOPBACK_PREFIX = "http://127.0.0.1:"


def site_blocks(caddyfile: str) -> dict[str, list[str]]:
    """Return ``{site address line: directive lines}`` for each site.

    A site starts at a column-0 line ending in ``{`` (the global
    options block, a bare ``{``, is skipped) and ends at the next
    column-0 ``}``. Directive lines are stripped; blank lines and
    comments are dropped.
    """
    blocks: dict[str, list[str]] = {}
    current = None
    for line in caddyfile.splitlines():
        if current is None:
            if line.endswith("{") and line[:1] not in ("", " ", "#", "{"):
                current = line[:-1].strip()
                blocks[current] = []
            continue
        if line == "}":
            current = None
            continue
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            blocks[current].append(stripped)
    return blocks


def split_sites(caddyfile: str, public: str, loopback: str):
    """Return ``(loopback site lines, public site lines)``.

    Fails unless the site addresses are exactly ``public`` and
    ``loopback``: an extra site (say ``http://localhost:<port>``
    without ``bind``) must not slip past these checks.
    """
    blocks = site_blocks(caddyfile)
    assert sorted(blocks) == sorted([public, loopback]), list(blocks)
    return blocks[loopback], blocks[public]


@pytest.fixture
def data_dir(tmp_path):
    tls = tmp_path / "tls"
    tls.mkdir()
    (tls / "cert.pem").write_text("CERT")
    (tls / "key.pem").write_text("KEY")
    return tmp_path


def render_manager(data_dir: Path) -> str:
    return render_manager_caddyfile(
        public_tls_port=8080, loopback_plaintext_port=8089,
        waitress_public_port=8090, waitress_internal_port=8088,
        cert_path=data_dir / "tls" / "cert.pem",
        key_path=data_dir / "tls" / "key.pem",
        manager_data_dir=data_dir,
    )


def render_worker(data_dir: Path) -> str:
    return render_worker_caddyfile(
        public_tls_port=8081, loopback_plaintext_port=8082,
        waitress_upstream_port=9081,
        cert_path=data_dir / "tls" / "cert.pem",
        key_path=data_dir / "tls" / "key.pem",
        worker_data_dir=data_dir,
    )


def docker_caddyfile(component: str) -> str:
    """Read a static Docker Caddyfile, or skip this case.

    ``deploy/`` is not copied into the Docker test image
    (``Dockerfile.test``), so these cases skip there; the template
    cases still run.
    """
    path = DOCKER_DIR / component / "Caddyfile"
    if not path.is_file():
        pytest.skip(f"{path} not present (deploy/ is not in the Docker test image)")
    return path.read_text(encoding="utf-8")


def _docker_sites(component: str) -> tuple[str, str]:
    prefix = component.upper()
    return (
        f":{{$SETHLANS_{prefix}_CADDY_PUBLIC_TLS_PORT}}",
        f"{LOOPBACK_PREFIX}{{$SETHLANS_{prefix}_CADDY_LOOPBACK_PORT}}",
    )


# name -> (loader, public site address, loopback site address)
SOURCES = {
    "manager-template": (render_manager, ":8080", f"{LOOPBACK_PREFIX}8089"),
    "worker-template": (render_worker, ":8081", f"{LOOPBACK_PREFIX}8082"),
    "docker-manager": (lambda d: docker_caddyfile("manager"), *_docker_sites("manager")),
    "docker-worker": (lambda d: docker_caddyfile("worker"), *_docker_sites("worker")),
}


def sites_for(source: str, data_dir: Path):
    loader, public, loopback = SOURCES[source]
    return split_sites(loader(data_dir), public, loopback)


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_loopback_site_starts_with_bind_loopback(source, data_dir):
    loopback, _ = sites_for(source, data_dir)
    assert loopback[0] == "bind 127.0.0.1", loopback
    assert sum(1 for line in loopback if line.startswith("bind")) == 1


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_public_site_has_no_bind(source, data_dir):
    _, public = sites_for(source, data_dir)
    assert not [line for line in public if line.startswith("bind")], public


def test_split_sites_rejects_an_extra_site():
    text = (
        ":8080 {\n    respond 200\n}\n"
        "http://127.0.0.1:8089 {\n    bind 127.0.0.1\n}\n"
        "http://localhost:8090 {\n    respond 200\n}\n"
    )
    with pytest.raises(AssertionError):
        split_sites(text, ":8080", "http://127.0.0.1:8089")


def test_site_blocks_parser_finds_both_sites():
    text = (
        "{\n    admin off\n}\n\n"
        ":8443 {\n    tls a b {\n        protocols tls1.2\n    }\n}\n\n"
        "# comment {\n"
        "http://127.0.0.1:{$PORT} {\n    bind 127.0.0.1\n\n    respond 404\n}\n"
    )
    blocks = site_blocks(text)
    assert list(blocks) == [":8443", "http://127.0.0.1:{$PORT}"]
    assert blocks["http://127.0.0.1:{$PORT}"] == ["bind 127.0.0.1", "respond 404"]
