# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Caddy in front of a setup-mode child, as the launcher runs it.

The Caddyfile is the shipped manager template
(``render_manager_caddyfile``) with its public vhost proxying to the
setup process's Waitress port. The binary is the pinned one
(``shared.frozen_paths.get_caddy_path``: ``.venv-build/caddy/`` from
source, ``python tools/fetch_caddy.py --target-dir .venv-build/caddy``);
callers skip when it is missing. Caddy's own config and data folders
point under the test's folder.
"""
import os
import socket
import ssl
import subprocess
import time
from pathlib import Path

import pytest

from sethlans_manager.caddy_template import render_manager_caddyfile
from shared.cert_utils import generate_self_signed_cert
from shared.frozen_paths import get_caddy_path

from .setup_process import free_port, request

READY_TIMEOUT_S = 30.0


def caddy_binary_or_skip() -> Path:
    path = Path(get_caddy_path())
    if not path.is_file():
        pytest.skip(f"Caddy binary not found at {path}")
    return path


def make_cert(folder: Path):
    """A self-signed certificate (SANs: 127.0.0.1, localhost, this machine's addresses)."""
    cert = folder / "tls" / "cert.pem"
    key = folder / "tls" / "key.pem"
    generate_self_signed_cert(cert, key)
    return cert, key


def client_context(cert: Path) -> ssl.SSLContext:
    """Trusts only the test certificate; host names are checked."""
    return ssl.create_default_context(cafile=str(cert))


def lan_address():
    """This machine's first non-loopback, non-link-local IPv4 address, or ``None``."""
    from sethlans_manager.setup_mode.net import ipv4_addresses

    for address in ipv4_addresses():
        if address.startswith("169.254."):
            continue
        # A listener on every interface, reached through ``address``: the
        # peer the listener sees is what Caddy will see.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("0.0.0.0", 0))
            server.listen(1)
            server.settimeout(2)
            try:
                with socket.create_connection((address, server.getsockname()[1]), timeout=2):
                    peer, _ = server.accept()
                    with peer:
                        if peer.getpeername()[0] == address:
                            return address
            except OSError:
                continue
    return None


class CaddyFront:
    """One Caddy process; ``folder`` holds its Caddyfile and logs, ``cert_root`` the certificate."""

    def __init__(self, binary: Path, folder: Path, cert: Path, key: Path, cert_root: Path):
        self.binary = binary
        self.folder = folder
        self.cert = cert
        self.key = key
        self.cert_root = cert_root
        self.port = free_port("0.0.0.0")
        self.proc = None
        self._outputs = ()

    def start(self, upstream_port: int) -> "CaddyFront":
        caddyfile = self.folder / "Caddyfile"
        caddyfile.write_text(render_manager_caddyfile(
            public_tls_port=self.port, loopback_plaintext_port=free_port(),
            waitress_public_port=upstream_port, waitress_internal_port=free_port(),
            cert_path=self.cert, key_path=self.key, manager_data_dir=self.cert_root,
        ), encoding="utf-8", newline="\n")
        home = self.folder / "caddy-home"
        home.mkdir(exist_ok=True)
        env = {**os.environ, "APPDATA": str(home), "XDG_CONFIG_HOME": str(home), "XDG_DATA_HOME": str(home)}
        out = open(self.folder / "caddy.stdout.log", "wb")
        err = open(self.folder / "caddy.stderr.log", "wb")
        self._outputs = (out, err)
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        self.proc = subprocess.Popen(  # nosec B603 - fixed argv
            [str(self.binary), "run", "--config", str(caddyfile), "--adapter", "caddyfile"],
            env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err, creationflags=flags)
        self._wait_ready()
        return self

    def _wait_ready(self) -> None:
        context = client_context(self.cert)
        deadline = time.monotonic() + READY_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"Caddy exited {self.proc.returncode}\n{self.output()}")
            try:
                if request(self.port, "GET", "/api/health/", ssl_context=context, timeout=2).status == 200:
                    return
            except (OSError, ssl.SSLError):
                pass
            time.sleep(0.2)
        raise TimeoutError(f"Caddy did not answer on {self.port}\n{self.output()}")

    def output(self) -> str:
        for handle in self._outputs:
            handle.flush()
        path = self.folder / "caddy.stderr.log"
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        for handle in self._outputs:
            handle.close()
        self._outputs = ()
