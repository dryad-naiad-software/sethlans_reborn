# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``tools/caddy_fetch/download.py``.

Covers ``sha256_of``, ``verify_sha256``, ``download_archive``,
``extract_archive``, and ``install_binary`` with mocked
``urllib.request.urlopen`` and in-memory archives.

The end-to-end ``fetch_and_install`` happy-path / idempotence tests
live in ``test_fetch_and_install.py`` so each file stays under the
300-line Python ceiling.
"""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest

from caddy_fetch import download as dl_mod
from caddy_fetch.exceptions import IntegrityError

from ._download_helpers import FakeResponse, build_targz, build_zip, sha256


# ---- sha256_of -----------------------------------------------------------


class TestSha256Of:

    def test_matches_python_hashlib(self, tmp_path):
        path = tmp_path / "blob.bin"
        content = b"hello caddy world" * 4096
        path.write_bytes(content)
        assert dl_mod.sha256_of(path) == sha256(content)

    def test_empty_file(self, tmp_path):
        path = tmp_path / "empty.bin"
        path.write_bytes(b"")
        assert dl_mod.sha256_of(path) == sha256(b"")


# ---- verify_sha256 -------------------------------------------------------


class TestVerifySha256:

    def test_matching_hash_returns_none(self, tmp_path):
        path = tmp_path / "a.bin"
        path.write_bytes(b"abc")
        assert dl_mod.verify_sha256(path, sha256(b"abc")) is None

    def test_case_insensitive_match(self, tmp_path):
        path = tmp_path / "a.bin"
        path.write_bytes(b"abc")
        dl_mod.verify_sha256(path, sha256(b"abc").upper())

    def test_mismatched_hash_raises_integrity_error(self, tmp_path):
        path = tmp_path / "a.bin"
        path.write_bytes(b"abc")
        with pytest.raises(IntegrityError, match="SHA-256 mismatch"):
            dl_mod.verify_sha256(path, "deadbeef" * 8)


# ---- download_archive ----------------------------------------------------


class TestDownloadArchive:

    def test_writes_payload_to_dest(self, tmp_path, mocker):
        payload = b"fake archive body"
        mock_urlopen = mocker.patch(
            "caddy_fetch.download.urllib.request.urlopen",
            return_value=FakeResponse(payload),
        )
        dest = tmp_path / "out" / "archive.tar.gz"
        dl_mod.download_archive("https://example.test/x.tar.gz", dest)
        assert dest.read_bytes() == payload
        mock_urlopen.assert_called_once()
        _, kwargs = mock_urlopen.call_args
        assert kwargs.get("timeout") == 120

    def test_creates_parent_directory(self, tmp_path, mocker):
        mocker.patch(
            "caddy_fetch.download.urllib.request.urlopen",
            return_value=FakeResponse(b"x"),
        )
        dest = tmp_path / "deeply" / "nested" / "out.bin"
        dl_mod.download_archive("https://example.test/out", dest)
        assert dest.parent.is_dir()
        assert dest.read_bytes() == b"x"

    def test_cleans_up_partial_on_error(self, tmp_path, mocker):
        """If urlopen raises, the .partial sidecar must not be left behind."""
        mocker.patch(
            "caddy_fetch.download.urllib.request.urlopen",
            side_effect=OSError("boom"),
        )
        dest = tmp_path / "out.bin"
        with pytest.raises(OSError):
            dl_mod.download_archive("https://example.test/x", dest)
        leftovers = list(tmp_path.glob("*.partial"))
        assert leftovers == []
        assert not dest.exists()


# ---- extract_archive -----------------------------------------------------


class TestExtractArchive:

    def test_extracts_tar_gz_and_returns_caddy_path(self, tmp_path):
        archive = build_targz(
            tmp_path, {"caddy": b"#!/fake\n", "LICENSE": b"text"},
        )
        dest = tmp_path / "out"
        result = dl_mod.extract_archive(archive, dest)
        assert result == dest / "caddy"
        assert result.is_file()
        assert result.read_bytes() == b"#!/fake\n"
        assert (dest / "LICENSE").is_file()

    def test_extracts_zip_and_returns_caddy_exe(self, tmp_path):
        archive = build_zip(
            tmp_path, {"caddy.exe": b"MZ\x00\x00", "README.txt": b"docs"},
        )
        dest = tmp_path / "out"
        result = dl_mod.extract_archive(archive, dest)
        assert result == dest / "caddy.exe"
        assert result.read_bytes() == b"MZ\x00\x00"

    def test_unsupported_archive_extension_raises(self, tmp_path):
        archive = tmp_path / "caddy.7z"
        archive.write_bytes(b"not a real archive")
        with pytest.raises(ValueError, match="Unsupported archive"):
            dl_mod.extract_archive(archive, tmp_path / "out")

    def test_archive_without_caddy_binary_raises(self, tmp_path):
        archive = build_targz(tmp_path, {"README": b"no binary here"})
        with pytest.raises(FileNotFoundError, match="No caddy binary"):
            dl_mod.extract_archive(archive, tmp_path / "out")

    def test_tar_path_traversal_is_refused(self, tmp_path):
        archive = tmp_path / "evil.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            info = tarfile.TarInfo(name="../evil")
            payload = b"pwned"
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        dest = tmp_path / "out"
        with pytest.raises(ValueError, match="escapes dest"):
            dl_mod.extract_archive(archive, dest)
        assert not (tmp_path / "evil").exists()

    def test_zip_path_traversal_is_refused(self, tmp_path):
        archive = tmp_path / "evil.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("../evil", b"pwned")
        dest = tmp_path / "out"
        with pytest.raises(ValueError, match="escapes dest"):
            dl_mod.extract_archive(archive, dest)
        assert not (tmp_path / "evil").exists()

    def test_sibling_path_collision_is_refused(self, tmp_path):
        """Regression: the old ``startswith`` containment check would
        accept sibling directories sharing a string prefix with the
        destination (e.g. ``.../caddy-evil/foo`` passes
        ``startswith(".../caddy")``). ``Path.relative_to`` rejects this
        correctly."""
        dest = tmp_path / "caddy"
        dest.mkdir()
        # Craft an archive whose member tries to write into the sibling
        # "caddy-evil" directory alongside dest.
        archive = tmp_path / "sibling.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            info = tarfile.TarInfo(name="../caddy-evil/payload")
            payload = b"sibling-escape"
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        with pytest.raises(ValueError, match="escapes dest"):
            dl_mod.extract_archive(archive, dest)
        assert not (tmp_path / "caddy-evil").exists()


# ---- install_binary ------------------------------------------------------


class TestInstallBinary:

    def test_copies_extracted_caddy_to_target(self, tmp_path):
        archive = build_targz(tmp_path, {"caddy": b"binary bytes"})
        target = tmp_path / "target" / "caddy"
        work = tmp_path / "work"
        result = dl_mod.install_binary(archive, target, work)
        assert result == target
        assert target.read_bytes() == b"binary bytes"
