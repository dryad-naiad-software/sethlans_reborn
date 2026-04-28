# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Issue #170 / AC-AbortedHandshakeRegression integration test.

This is THE regression test that motivates the wizard Caddy
consolidation. The original wizard wrapped its listening socket with
``ssl.SSLContext.wrap_socket(server_side=True)`` and handed the
TLS-wrapped listener to waitress. After a handful of browser-aborted
handshakes (Chromium showing the privacy interstitial, then dismissing
or closing the tab) waitress's asyncore accept loop wedged and the
listener stopped accepting — the wizard became unreachable.

Manager + worker dodged the problem because Caddy fronts them.
Issue #170 consolidates the wizard onto the same architecture: Caddy
binds the public TLS port, the wizard subprocess listens plain HTTP on
loopback. A production-grade reverse proxy handles aborted handshakes
gracefully, so subsequent connections still succeed.

The test:
1. Spins up a Caddy supervisor + wizard subprocess pair (mirrors the
   launcher's first-run flow without the launcher's tray + splash).
2. Fires N=20 connections that close mid-handshake (raw TCP connect →
   close, no TLS bytes ever sent).
3. Asserts a normal HTTPS GET ``/api/health/`` returns 200 afterward.

If this test ever fails the consolidation has regressed and the
wizard is unreachable to real users after they dismiss the privacy
interstitial.

Process-spawn helpers live in ``_aborted_handshakes_helpers.py`` so
this file stays under the 300-line ceiling.
"""

from __future__ import annotations

import json
import secrets
import shutil
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from shared.cert_utils import generate_self_signed_cert
from shared.frozen_paths import get_caddy_path

from ._aborted_handshakes_helpers import (
    READY_TIMEOUT_S,
    abort_handshake,
    drain,
    free_loopback_port,
    no_verify_ctx,
    spawn_wizard,
    terminate,
    wait_for_caddy_ready,
    wait_for_loopback_ready,
    write_secret,
)

HANDSHAKE_ABORT_COUNT = 20
HANDSHAKE_FOLLOWUP_TIMEOUT_S = 10.0


@pytest.fixture
def caddy_binary():
    """Skip if a Caddy binary is not available in this checkout."""
    try:
        path = Path(get_caddy_path())
    except Exception:
        pytest.skip("Caddy binary path not resolvable")
    if not path.is_file():
        pytest.skip(f"Caddy binary not found at {path}")
    return path


@pytest.fixture
def wizard_caddy_pair(tmp_path, caddy_binary):
    """Spin up a wizard + Caddy pair that mirrors the launcher's flow.

    Yields ``(public_port, wizard_subdir)`` and tears both down on
    teardown.
    """
    from launcher.wizard_caddy_wiring import build_wizard_caddy_supervisor

    data_dir = tmp_path / "data"
    wizard_subdir = data_dir / "wizard"
    wizard_subdir.mkdir(parents=True)

    setup_token = secrets.token_urlsafe(32)
    ipc_secret = secrets.token_urlsafe(32).encode("ascii")
    write_secret(
        wizard_subdir / ".setup_token", setup_token.encode("ascii"),
    )
    write_secret(wizard_subdir / ".ipc_secret", ipc_secret)

    cert_path = wizard_subdir / "tls" / "cert.pem"
    key_path = wizard_subdir / "tls" / "key.pem"
    generate_self_signed_cert(cert_path, key_path)

    loopback_port = free_loopback_port()
    public_port = free_loopback_port()
    while public_port == loopback_port:
        public_port = free_loopback_port()

    wizard_proc = spawn_wizard(data_dir, loopback_port)
    supervisor = None
    try:
        deadline = time.monotonic() + READY_TIMEOUT_S
        if not wait_for_loopback_ready(loopback_port, wizard_proc, deadline):
            stdout, stderr = drain(wizard_proc)
            raise RuntimeError(
                f"wizard did not become ready on loopback port "
                f"{loopback_port}\n--- stdout ---\n{stdout}\n"
                f"--- stderr ---\n{stderr}"
            )

        supervisor = build_wizard_caddy_supervisor(
            caddyfile_path=wizard_subdir / "Caddyfile",
            public_tls_port=public_port,
            loopback_port=loopback_port,
            cert_path=cert_path,
            key_path=key_path,
            wizard_data_dir=wizard_subdir,
            binary_path=caddy_binary,
        )
        supervisor.start()
        if not wait_for_caddy_ready(
            public_port, time.monotonic() + READY_TIMEOUT_S,
        ):
            raise RuntimeError(
                f"Caddy did not become ready on public port {public_port}"
            )

        yield public_port, wizard_subdir
    finally:
        if supervisor is not None:
            try:
                supervisor.stop(timeout=5.0)
            except Exception:
                pass
        terminate(wizard_proc)
        if wizard_proc.returncode == 0:
            shutil.rmtree(data_dir, ignore_errors=True)


# ---------------------------------------------------------------------
# AC-AbortedHandshakeRegression — N=20 aborted handshakes
# ---------------------------------------------------------------------

@pytest.mark.timeout(120)
def test_aborted_handshakes_do_not_lock_listener(wizard_caddy_pair):
    """Issue #170 FR-10: N aborted handshakes don't break the listener.

    Mid-handshake aborts used to wedge waitress's asyncore loop after
    a few iterations. With Caddy in front, the public listener is
    Caddy's — and Caddy handles aborts gracefully. After 20 aborts a
    normal HTTPS GET to ``/api/health/`` MUST still return 200.
    """
    public_port, _ = wizard_caddy_pair

    for _ in range(HANDSHAKE_ABORT_COUNT):
        abort_handshake(public_port)

    # Final assertion: subsequent normal request still succeeds.
    ctx = no_verify_ctx()
    deadline = time.monotonic() + HANDSHAKE_FOLLOWUP_TIMEOUT_S
    last_err = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"https://127.0.0.1:{public_port}/api/health/",
                timeout=2, context=ctx,
            ) as resp:
                assert resp.status == 200, resp.status
                payload = json.loads(resp.read().decode("utf-8"))
                assert "boot_id" in payload and payload["boot_id"]
                assert "version" in payload and payload["version"]
                return
        except (
            urllib.error.URLError, ConnectionError,
            OSError, ssl.SSLError,
        ) as exc:
            last_err = exc
        time.sleep(0.25)
    pytest.fail(
        f"GET /api/health/ did not succeed within "
        f"{HANDSHAKE_FOLLOWUP_TIMEOUT_S}s after "
        f"{HANDSHAKE_ABORT_COUNT} aborted handshakes; "
        f"last_err={last_err!r}"
    )
