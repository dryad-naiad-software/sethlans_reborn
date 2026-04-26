# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Wizard subprocess boot + secret-file unlink integration tests.

Covers Spec 1 / D1 scenarios 1 (binary boots and serves token entry
page) and 8 (setup token + IPC secret files unlinked after the wizard
has read them — SEC-MED-11).
"""

from __future__ import annotations

from . import _http


def test_wizard_boots_and_serves_index(wizard_process):
    """Scenario 1: a fresh wizard subprocess serves the token-entry page."""
    status, headers, body = _http.get(f"{wizard_process.base_url}/")
    assert status == 200, body
    body_text = body.decode("utf-8", errors="replace")
    assert "<title>Sethlans" in body_text, body_text[:400]
    # FR-W-FE2 security headers are non-negotiable; spot-check a couple.
    assert headers.get("Content-Security-Policy"), headers
    assert headers.get("X-Content-Type-Options") == "nosniff"


def test_wizard_unlinks_setup_token_and_ipc_secret(wizard_process):
    """Scenario 8: SEC-MED-11 — secret files removed once the wizard reads.

    The fixture provisions the chmod-600 secret files BEFORE spawning
    the wizard. By the time the readiness probe returns 200 the wizard
    has already read both files into process memory and unlinked them
    from disk per :func:`wizard.sethlans_wizard.ipc.read_secret_file`.
    """
    setup_token_path = wizard_process.wizard_subdir / ".setup_token"
    ipc_secret_path = wizard_process.wizard_subdir / ".ipc_secret"
    assert not setup_token_path.exists(), (
        f"setup token file should have been unlinked, still at {setup_token_path}"
    )
    assert not ipc_secret_path.exists(), (
        f"IPC secret file should have been unlinked, still at {ipc_secret_path}"
    )


def test_wizard_writes_port_file(wizard_process):
    """The bootstrap writes ``<wizard_subdir>/port`` per FR-CFG2."""
    port_file = wizard_process.wizard_subdir / "port"
    assert port_file.is_file(), f"expected port file at {port_file}"
    contents = port_file.read_text(encoding="ascii").strip()
    assert contents == str(wizard_process.port), contents
