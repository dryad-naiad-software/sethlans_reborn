# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Optional GPG signature verification.

The Caddy project does not publish per-archive ``.asc`` signatures for
v2.8.4 — in the default (sig-less) mode the ``--verify-gpg`` flag is a
logged warning and a return. The hook is wired so that a future
lockfile carrying a per-platform ``sig_url`` field activates the real
verification path.

Behavior contract (per the Phase 1 spec):
  * ``verify_gpg`` takes the downloaded **archive** path (NOT the
    post-extract binary — detached ``.asc`` signatures are over the
    archive file upstream ships).
  * If no signature URL is configured (``signature_url`` is falsy),
    log a best-effort warning and return — the fetch continues under
    SHA-256 integrity alone.
  * If a signature URL IS configured but ``gpg`` is not on PATH, log
    a warning and return (fail-open for dev workstations).
  * If a signature URL IS configured AND gpg IS on PATH, download the
    ``.asc`` and shell out to ``gpg --verify``. Any failure — network
    404 on the sig, gpg subprocess non-zero, unexpected error — is
    wrapped in :class:`GpgVerificationError` (which the CLI layer
    maps to exit code 3).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from .exceptions import GpgVerificationError

logger = logging.getLogger("fetch_caddy")


def verify_gpg(archive: Path, signature_url: str | None) -> None:
    """Best-effort GPG signature verification over ``archive``.

    ``archive`` is the downloaded archive (tar.gz / zip), not the
    extracted binary — upstream ``.asc`` signatures are over the
    archive blob. Called BEFORE extraction from ``fetch_and_install``.
    """
    if not signature_url:
        logger.warning(
            "--verify-gpg requested but no detached signature URL is "
            "configured for this platform (Caddy v2.8.4 upstream does "
            "not ship per-archive signatures). Proceeding under "
            "SHA-256 integrity alone."
        )
        return
    if shutil.which("gpg") is None:
        logger.warning(
            "--verify-gpg requested but gpg binary not on PATH; "
            "skipping GPG check (SHA-256 integrity check still enforced)"
        )
        return
    _run_gpg_verify(archive, signature_url)


def _run_gpg_verify(archive: Path, signature_url: str) -> None:
    """Download ``signature_url`` and run ``gpg --verify``.

    Raises :class:`GpgVerificationError` on any failure so the CLI
    layer surfaces exit code 3.
    """
    with tempfile.TemporaryDirectory(prefix="gpg_verify_") as td:
        sig_path = Path(td) / "archive.asc"
        try:
            with urllib.request.urlopen(signature_url, timeout=60) as resp:
                sig_path.write_bytes(resp.read())
        except (urllib.error.URLError, OSError) as exc:
            raise GpgVerificationError(
                f"Failed to download signature from {signature_url}: {exc}"
            ) from exc
        try:
            subprocess.run(
                ["gpg", "--verify", str(sig_path), str(archive)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or b"").decode("utf-8", errors="replace")
            raise GpgVerificationError(
                f"gpg --verify failed for {archive.name}: {stderr.strip()}"
            ) from exc
        except (OSError, FileNotFoundError) as exc:
            raise GpgVerificationError(
                f"gpg subprocess invocation failed: {exc}"
            ) from exc
    logger.info("GPG signature verified for %s", archive.name)
