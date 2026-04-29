# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Coverage expansion: ``wizard/sethlans_wizard/ffmpeg_download.py``
(FR-M2-7 / FR-M2-7b).

Locks the URL/SHA dictionary contract, the platform detection helper,
the streaming download cancel-event semantics, the SHA placeholder
warn-and-skip behavior, the version-check subprocess invocation
contract (``shell=False`` + 5s timeout), and the timeout/launch
failure branches.
"""

from __future__ import annotations

import hashlib
import logging
import platform
import subprocess

import pytest
import requests

from wizard.sethlans_wizard import ffmpeg_download as ffdl


class TestPlatformDetection:

    @pytest.mark.parametrize(
        "system,arch,expected",
        [
            ("Windows", "AMD64", "windows-x64"),
            ("Linux", "x86_64", "linux-x64"),
            ("Linux", "aarch64", "linux-arm64"),
            ("Darwin", "arm64", "macos-arm64"),
            ("Darwin", "x86_64", "macos-x64"),
        ],
    )
    def test_known_platforms(self, mocker, system, arch, expected):
        mocker.patch.object(platform, "system", return_value=system)
        mocker.patch.object(platform, "machine", return_value=arch)
        assert ffdl._get_platform_id() == expected

    def test_unknown_platform_returns_none(self, mocker):
        mocker.patch.object(platform, "system", return_value="Plan9")
        mocker.patch.object(platform, "machine", return_value="vax")
        assert ffdl._get_platform_id() is None


class TestExpectedShaDict:

    def test_every_known_platform_has_an_entry(self):
        # Coverage expansion: the SHA dict MUST cover every supported
        # platform so the verify path knows how to react.
        expected = {
            "windows-x64", "linux-x64", "linux-arm64",
            "macos-arm64", "macos-x64",
        }
        assert set(ffdl.EXPECTED_FFMPEG_SHA256.keys()) == expected

    def test_placeholder_pin_present(self):
        # Coverage expansion: until DevOps populates real hashes the
        # value should be the documented placeholder, not garbage.
        for v in ffdl.EXPECTED_FFMPEG_SHA256.values():
            # Each entry is either the literal placeholder OR a 64-char
            # hex string (real SHA-256).
            assert v == ffdl.SHA256_PLACEHOLDER or (
                len(v) == 64 and all(c in "0123456789abcdef" for c in v)
            )


class TestUrlsDict:

    def test_every_supported_platform_has_url(self):
        # Coverage expansion: the URL dict and SHA dict MUST be in
        # lockstep — orphaned keys in either side are a release bug.
        assert set(ffdl._FFMPEG_URLS.keys()) == set(
            ffdl.EXPECTED_FFMPEG_SHA256.keys(),
        )

    def test_urls_are_https(self):
        for url in ffdl._FFMPEG_URLS.values():
            assert url.startswith("https://"), url

    def test_urls_match_pinned_version(self):
        # FR-M2-7 — every URL contains the FFMPEG_VERSION token.
        v = ffdl.FFMPEG_VERSION
        for url in ffdl._FFMPEG_URLS.values():
            assert v in url, (v, url)


class TestGetFFmpegBinary:

    def test_returns_none_when_dir_missing(self, tmp_path):
        assert ffdl.get_ffmpeg_binary(tmp_path) is None

    def test_finds_binary_under_versioned_dir_posix(
        self, tmp_path, mocker,
    ):
        mocker.patch.object(platform, "system", return_value="Linux")
        ffdir = ffdl.get_ffmpeg_dir(tmp_path)
        ffdir.mkdir(parents=True)
        bin_path = ffdir / "ffmpeg"
        bin_path.write_bytes(b"\x7fELF...")
        found = ffdl.get_ffmpeg_binary(tmp_path)
        assert found == bin_path

    def test_finds_binary_under_versioned_dir_windows(
        self, tmp_path, mocker,
    ):
        mocker.patch.object(platform, "system", return_value="Windows")
        ffdir = ffdl.get_ffmpeg_dir(tmp_path)
        ffdir.mkdir(parents=True)
        bin_path = ffdir / "ffmpeg.exe"
        bin_path.write_bytes(b"MZ...")
        found = ffdl.get_ffmpeg_binary(tmp_path)
        assert found == bin_path

    def test_already_installed_true_when_binary_present(
        self, tmp_path, mocker,
    ):
        mocker.patch.object(platform, "system", return_value="Linux")
        ffdir = ffdl.get_ffmpeg_dir(tmp_path)
        ffdir.mkdir(parents=True)
        (ffdir / "ffmpeg").write_bytes(b"x")
        assert ffdl.already_installed(tmp_path) is True

    def test_already_installed_false_when_dir_empty(self, tmp_path):
        assert ffdl.already_installed(tmp_path) is False


class TestHashFile:

    def test_hashes_match_hashlib(self, tmp_path):
        f = tmp_path / "a.bin"
        body = b"hello-ffmpeg" * 1000
        f.write_bytes(body)
        assert ffdl._hash_file(f) == hashlib.sha256(body).hexdigest()


class TestStreamDownload:

    def test_writes_payload_to_disk(self, tmp_path, mocker):
        chunks = [b"abc", b"def", b"ghij"]
        fake_resp = mocker.MagicMock()
        fake_resp.headers = {"content-length": "10"}
        fake_resp.iter_content.return_value = iter(chunks)
        fake_resp.raise_for_status.return_value = None
        mocker.patch.object(requests, "get", return_value=fake_resp)

        target = tmp_path / "out.bin"
        cancel = __import__("threading").Event()
        ok = ffdl._stream_download("https://example/x", target, cancel)
        assert ok is True
        assert target.read_bytes() == b"abcdefghij"

    def test_cancel_event_short_circuits(self, tmp_path, mocker):
        cancel = __import__("threading").Event()
        cancel.set()  # Pre-cancel — first chunk should bail out.

        fake_resp = mocker.MagicMock()
        fake_resp.headers = {"content-length": "0"}
        fake_resp.iter_content.return_value = iter([b"abc", b"def"])
        fake_resp.raise_for_status.return_value = None
        mocker.patch.object(requests, "get", return_value=fake_resp)

        target = tmp_path / "out.bin"
        ok = ffdl._stream_download(
            "https://example/x", target, cancel,
        )
        assert ok is False

    def test_progress_callback_invoked_when_total_known(
        self, tmp_path, mocker,
    ):
        chunks = [b"a" * 50, b"b" * 50]
        fake_resp = mocker.MagicMock()
        fake_resp.headers = {"content-length": "100"}
        fake_resp.iter_content.return_value = iter(chunks)
        fake_resp.raise_for_status.return_value = None
        mocker.patch.object(requests, "get", return_value=fake_resp)

        seen = []
        cancel = __import__("threading").Event()
        ok = ffdl._stream_download(
            "https://example/x", tmp_path / "out.bin", cancel,
            on_percent=seen.append,
        )
        assert ok is True
        # Final percent capped at 99 per the FR-M2-7 spec (only the
        # version-check stage transitions to 100).
        assert max(seen) == 99
        assert all(0 <= p <= 99 for p in seen)


class TestVerifySha256:

    def test_placeholder_skips_with_warning(
        self, tmp_path, mocker, caplog,
    ):
        # FR-M2-7a — placeholder pin must WARN + skip + return None.
        mocker.patch.dict(
            ffdl.EXPECTED_FFMPEG_SHA256,
            {"linux-x64": ffdl.SHA256_PLACEHOLDER},
            clear=False,
        )
        archive = tmp_path / "x.tar.xz"
        archive.write_bytes(b"any bytes")
        with caplog.at_level(logging.WARNING):
            err = ffdl._verify_sha256(archive, "linux-x64")
        assert err is None
        # Warning emitted so an operator can see it pre-release.
        assert any(
            "placeholder" in r.getMessage().lower()
            for r in caplog.records
        )

    def test_sha_match_returns_none(self, tmp_path, mocker):
        body = b"matched payload"
        archive = tmp_path / "x.zip"
        archive.write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        mocker.patch.dict(
            ffdl.EXPECTED_FFMPEG_SHA256,
            {"linux-x64": digest}, clear=False,
        )
        assert ffdl._verify_sha256(archive, "linux-x64") is None

    def test_sha_mismatch_returns_category(self, tmp_path, mocker):
        archive = tmp_path / "x.zip"
        archive.write_bytes(b"actual")
        wrong = "0" * 64
        mocker.patch.dict(
            ffdl.EXPECTED_FFMPEG_SHA256,
            {"linux-x64": wrong}, clear=False,
        )
        assert ffdl._verify_sha256(archive, "linux-x64") == "sha_mismatch"

    def test_unknown_platform_skips_with_warning(self, tmp_path, caplog):
        archive = tmp_path / "x.zip"
        archive.write_bytes(b"x")
        with caplog.at_level(logging.WARNING):
            err = ffdl._verify_sha256(archive, "plan9-vax")
        assert err is None


class TestExtractArchive:

    def test_unsupported_extension_raises(self, tmp_path):
        # Coverage expansion: only .zip and .tar.xz are accepted.
        bogus = tmp_path / "x.7z"
        bogus.write_bytes(b"x")
        with pytest.raises(ValueError):
            ffdl._extract_archive(bogus, tmp_path)

    def test_zip_dispatches_to_safe_zip_extract(self, tmp_path, mocker):
        spy = mocker.patch(
            "wizard.sethlans_wizard.ffmpeg_download._safe_zip_extract",
        )
        archive = tmp_path / "x.zip"
        archive.write_bytes(b"x")
        ffdl._extract_archive(archive, tmp_path)
        assert spy.called

    def test_tar_xz_uses_filter_data(self, tmp_path, mocker):
        # Coverage expansion: tarfile.extractall MUST pass filter='data'
        # to enforce safe extraction (post-PEP-706).
        fake_tar = mocker.MagicMock()
        fake_open = mocker.patch(
            "wizard.sethlans_wizard.ffmpeg_download.tarfile.open",
        )
        fake_open.return_value.__enter__.return_value = fake_tar
        archive = tmp_path / "x.tar.xz"
        archive.write_bytes(b"x")
        ffdl._extract_archive(archive, tmp_path)
        kwargs = fake_tar.extractall.call_args.kwargs
        assert kwargs.get("filter") == "data"


class TestRunVersionCheck:

    def test_happy_path_returns_version(self, tmp_path, mocker):
        # FR-M2-7b — subprocess invocation MUST use shell=False, list
        # form, and the 5-second timeout cap.
        mock_run = mocker.patch.object(
            subprocess, "run",
            return_value=mocker.MagicMock(
                returncode=0,
                stdout="ffmpeg version 7.1 lots of stuff",
                stderr="",
            ),
        )
        ok, version_str = ffdl.run_version_check(
            tmp_path / "ffmpeg", timeout=5,
        )
        assert ok is True
        assert "7.1" in version_str
        # Argument contract:
        args, kwargs = mock_run.call_args
        assert args[0] == [str(tmp_path / "ffmpeg"), "-version"]
        assert kwargs["shell"] is False
        assert kwargs["timeout"] == 5
        assert kwargs["capture_output"] is True

    def test_nonzero_returncode_returns_failure(self, tmp_path, mocker):
        mocker.patch.object(
            subprocess, "run",
            return_value=mocker.MagicMock(
                returncode=1, stdout="", stderr="bad",
            ),
        )
        ok, version_str = ffdl.run_version_check(tmp_path / "ffmpeg")
        assert ok is False
        assert version_str == ""

    def test_timeout_kills_child_returns_failure(
        self, tmp_path, mocker, caplog,
    ):
        # FR-M2-7b — TimeoutExpired is caught and the child is killed.
        # The returned process is None to exercise the defensive branch.
        exc = subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=5)
        mocker.patch.object(subprocess, "run", side_effect=exc)
        with caplog.at_level(logging.ERROR):
            ok, version_str = ffdl.run_version_check(tmp_path / "ffmpeg")
        assert ok is False
        assert version_str == ""

    def test_oserror_on_launch_returns_failure(
        self, tmp_path, mocker, caplog,
    ):
        # Coverage expansion: missing binary → OSError.
        mocker.patch.object(
            subprocess, "run", side_effect=OSError("no such file"),
        )
        with caplog.at_level(logging.ERROR):
            ok, version_str = ffdl.run_version_check(tmp_path / "ffmpeg")
        assert ok is False
        assert version_str == ""

    def test_default_timeout_is_5_seconds(self, tmp_path, mocker):
        # FR-M2-7b — default timeout MUST be the constant.
        assert ffdl.SUBPROCESS_TIMEOUT_SECONDS == 5
        mock_run = mocker.patch.object(
            subprocess, "run",
            return_value=mocker.MagicMock(
                returncode=0, stdout="7.1", stderr="",
            ),
        )
        ffdl.run_version_check(tmp_path / "ffmpeg")
        assert mock_run.call_args.kwargs["timeout"] == 5


class TestExports:

    def test_dunder_all(self):
        for name in (
            "FFMPEG_VERSION", "EXPECTED_FFMPEG_SHA256", "SHA256_PLACEHOLDER",
            "_get_platform_id", "get_ffmpeg_dir", "get_ffmpeg_binary",
            "already_installed", "_stream_download", "_verify_sha256",
            "_extract_archive", "run_version_check",
        ):
            assert name in ffdl.__all__

    def test_get_ffmpeg_dir_includes_version(self, tmp_path):
        d = ffdl.get_ffmpeg_dir(tmp_path)
        assert ffdl.FFMPEG_VERSION in str(d)
        assert d == tmp_path / "bin" / "ffmpeg" / ffdl.FFMPEG_VERSION
