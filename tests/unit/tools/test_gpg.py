# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``tools/caddy_fetch/gpg.py``.

The current production state (Caddy v2.8.4) ships no detached
``.asc`` signatures, so the default call path is a logged no-op.
These tests pin:

* the "no signature configured" branch (``signature_url`` falsy),
* the "gpg not on PATH" branch,
* the real ``gpg --verify`` branch — signature download failure and
  ``gpg`` subprocess failure both raise
  :class:`GpgVerificationError`, making exit code 3 reachable.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from caddy_fetch import gpg as gpg_mod
from caddy_fetch.exceptions import GpgVerificationError


class TestVerifyGpgNoOpBranches:
    """Calls that log a warning and return ``None`` (do not raise)."""

    def test_empty_signature_url_returns_none(self, mocker, caplog):
        """No per-platform sig URL configured → warn and proceed."""
        mocker.patch("caddy_fetch.gpg.shutil.which", return_value="/usr/bin/gpg")
        with caplog.at_level(logging.WARNING, logger="fetch_caddy"):
            result = gpg_mod.verify_gpg(Path("fake.tar.gz"), "")
        assert result is None
        assert any(
            "no detached signature URL is configured" in r.getMessage()
            for r in caplog.records
        )

    def test_none_signature_url_returns_none(self, caplog):
        with caplog.at_level(logging.WARNING, logger="fetch_caddy"):
            result = gpg_mod.verify_gpg(Path("fake.tar.gz"), None)
        assert result is None

    def test_gpg_not_on_path_returns_none(self, mocker, caplog):
        mocker.patch("caddy_fetch.gpg.shutil.which", return_value=None)
        with caplog.at_level(logging.WARNING, logger="fetch_caddy"):
            result = gpg_mod.verify_gpg(
                Path("fake.tar.gz"),
                "https://example.test/sig.asc",
            )
        assert result is None
        assert any(
            "gpg binary not on PATH" in r.getMessage()
            for r in caplog.records
        )


class TestVerifyGpgRaisesExitCode3Path:
    """Branches that MUST raise ``GpgVerificationError`` so the CLI
    surfaces exit code 3. Keeps that exit code reachable under unit
    tests even though Caddy v2.8.4 ships no signatures today."""

    def test_signature_download_failure_raises(self, mocker, tmp_path):
        """Synthetic condition: a lockfile with ``sig_url`` + ``gpg``
        on PATH + a network-level failure fetching the ``.asc`` file
        must raise ``GpgVerificationError`` (mapped to exit 3)."""
        mocker.patch(
            "caddy_fetch.gpg.shutil.which", return_value="/usr/bin/gpg",
        )
        import urllib.error
        mocker.patch(
            "caddy_fetch.gpg.urllib.request.urlopen",
            side_effect=urllib.error.URLError("signature 404"),
        )
        archive = tmp_path / "caddy.tar.gz"
        archive.write_bytes(b"archive-bytes")
        with pytest.raises(GpgVerificationError, match="signature"):
            gpg_mod.verify_gpg(archive, "https://example.test/sig.asc")

    def test_gpg_subprocess_failure_raises(self, mocker, tmp_path):
        """When ``gpg --verify`` exits non-zero, wrap in
        ``GpgVerificationError`` so the CLI surfaces exit 3."""
        mocker.patch(
            "caddy_fetch.gpg.shutil.which", return_value="/usr/bin/gpg",
        )

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"-----BEGIN PGP SIGNATURE-----\nfake\n"

        mocker.patch(
            "caddy_fetch.gpg.urllib.request.urlopen",
            return_value=_FakeResp(),
        )
        mocker.patch(
            "caddy_fetch.gpg.subprocess.run",
            side_effect=subprocess.CalledProcessError(
                returncode=1,
                cmd=["gpg", "--verify"],
                stderr=b"BAD signature from unknown key",
            ),
        )
        archive = tmp_path / "caddy.tar.gz"
        archive.write_bytes(b"archive-bytes")
        with pytest.raises(GpgVerificationError, match="gpg --verify failed"):
            gpg_mod.verify_gpg(archive, "https://example.test/sig.asc")

    def test_gpg_subprocess_success_returns_none(self, mocker, tmp_path):
        """Happy path: sig download OK + ``gpg --verify`` exit 0."""
        mocker.patch(
            "caddy_fetch.gpg.shutil.which", return_value="/usr/bin/gpg",
        )

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"fake-sig-bytes"

        mocker.patch(
            "caddy_fetch.gpg.urllib.request.urlopen",
            return_value=_FakeResp(),
        )
        mocker.patch(
            "caddy_fetch.gpg.subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=["gpg"], returncode=0, stdout=b"", stderr=b"",
            ),
        )
        archive = tmp_path / "caddy.tar.gz"
        archive.write_bytes(b"archive-bytes")
        result = gpg_mod.verify_gpg(archive, "https://example.test/sig.asc")
        assert result is None
