# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``caddy_fetch.download.fetch_and_install``.

End-to-end (module-local): download (mocked) -> verify -> extract ->
install.  Split out from ``test_download.py`` to keep each file under
the 300-line Python ceiling.
"""

from __future__ import annotations

import pytest

from caddy_fetch import download as dl_mod
from caddy_fetch.exceptions import IntegrityError

from ._download_helpers import FakeResponse, build_targz, sha256


def _patch_urlopen(mocker, payload: bytes):
    return mocker.patch(
        "caddy_fetch.download.urllib.request.urlopen",
        return_value=FakeResponse(payload),
    )


class TestFetchAndInstallHappyPath:

    def test_installs_binary_when_target_absent(self, tmp_path, mocker):
        archive_bytes = build_targz(
            tmp_path, {"caddy": b"hello caddy"}, name="src.tar.gz",
        ).read_bytes()
        mock_urlopen = _patch_urlopen(mocker, archive_bytes)

        target = tmp_path / "dest" / "caddy"
        result = dl_mod.fetch_and_install(
            "https://example.test/caddy.tar.gz",
            sha256(archive_bytes),
            target,
        )
        assert result == target
        assert target.is_file()
        assert target.read_bytes() == b"hello caddy"
        mock_urlopen.assert_called_once()

    def test_missing_target_triggers_download(self, tmp_path, mocker):
        archive_bytes = build_targz(
            tmp_path, {"caddy": b"fresh"}, name="src.tar.gz",
        ).read_bytes()
        mock_urlopen = _patch_urlopen(mocker, archive_bytes)

        target = tmp_path / "dest" / "caddy"
        assert not target.exists()
        dl_mod.fetch_and_install(
            "https://example.test/caddy.tar.gz",
            sha256(archive_bytes),
            target,
        )
        assert target.read_bytes() == b"fresh"
        mock_urlopen.assert_called_once()


class TestFetchAndInstallSha256Mismatch:

    def test_mismatch_raises_integrity_error(self, tmp_path, mocker):
        archive_bytes = build_targz(
            tmp_path, {"caddy": b"data"}, name="src.tar.gz",
        ).read_bytes()
        _patch_urlopen(mocker, archive_bytes)
        target = tmp_path / "dest" / "caddy"
        with pytest.raises(IntegrityError, match="SHA-256 mismatch"):
            dl_mod.fetch_and_install(
                "https://example.test/caddy.tar.gz",
                "deadbeef" * 8,
                target,
            )
        assert not target.exists()


class TestFetchAndInstallIdempotence:
    """Idempotent short-circuit behavior — see fetch_and_install docstring.

    Fast-path contract (post-hardening):
      * existing file whose SHA-256 matches ``expected_sha256`` → skip
        download (happy-path idempotence).
      * existing file whose SHA-256 does NOT match → treat on-disk file
        as invalidated (stale or tampered) and fall through to a fresh
        download. Only a mismatch on the freshly-downloaded archive
        raises :class:`IntegrityError`.
    """

    def test_skip_when_existing_binary_matches_expected_sha(
        self, tmp_path, mocker,
    ):
        """Happy path: on-disk binary's SHA matches lockfile hash → skip."""
        target = tmp_path / "dest" / "caddy"
        target.parent.mkdir(parents=True)
        payload = b"legitimate binary"
        target.write_bytes(payload)

        mock_urlopen = mocker.patch(
            "caddy_fetch.download.urllib.request.urlopen",
            side_effect=AssertionError("urlopen must not be called"),
        )
        result = dl_mod.fetch_and_install(
            "https://example.test/caddy.tar.gz",
            sha256(payload),
            target,
        )
        assert result == target
        assert target.read_bytes() == payload
        mock_urlopen.assert_not_called()

    def test_tampered_on_disk_binary_triggers_redownload(
        self, tmp_path, mocker,
    ):
        """Fast-path hardening (Security M1 / DevOps M3): if the
        existing file's SHA-256 does NOT match the expected lockfile
        hash (e.g. a poisoned ``actions/cache`` entry, or a dev binary
        that survived a lockfile bump), we treat the on-disk file as
        invalidated and re-download rather than trusting the cached
        bytes."""
        archive_bytes = build_targz(
            tmp_path, {"caddy": b"fresh-legit"}, name="src.tar.gz",
        ).read_bytes()
        mock_urlopen = _patch_urlopen(mocker, archive_bytes)

        target = tmp_path / "dest" / "caddy"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"tampered")

        dl_mod.fetch_and_install(
            "https://example.test/caddy.tar.gz",
            sha256(archive_bytes),
            target,
        )
        mock_urlopen.assert_called_once()
        assert target.read_bytes() == b"fresh-legit"

    def test_fastpath_mismatch_then_download_mismatch_raises(
        self, tmp_path, mocker,
    ):
        """Fast-path mismatch + download mismatch → raises IntegrityError.

        Confirms the on-disk hash check does not mask a real supply-chain
        failure: if we fall through to re-download and the downloaded
        archive ALSO mismatches, we surface an ``IntegrityError``.
        """
        archive_bytes = build_targz(
            tmp_path, {"caddy": b"data"}, name="src.tar.gz",
        ).read_bytes()
        _patch_urlopen(mocker, archive_bytes)

        target = tmp_path / "dest" / "caddy"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"tampered")

        with pytest.raises(IntegrityError, match="SHA-256 mismatch"):
            dl_mod.fetch_and_install(
                "https://example.test/caddy.tar.gz",
                "deadbeef" * 8,  # neither on-disk nor archive hashes to this
                target,
            )

    def test_idempotent_skip_false_forces_download(self, tmp_path, mocker):
        archive_bytes = build_targz(
            tmp_path, {"caddy": b"fresh2"}, name="src.tar.gz",
        ).read_bytes()
        mock_urlopen = _patch_urlopen(mocker, archive_bytes)

        target = tmp_path / "dest" / "caddy"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"old-stale")
        dl_mod.fetch_and_install(
            "https://example.test/caddy.tar.gz",
            sha256(archive_bytes),
            target,
            idempotent_skip=False,
        )
        mock_urlopen.assert_called_once()
        assert target.read_bytes() == b"fresh2"
