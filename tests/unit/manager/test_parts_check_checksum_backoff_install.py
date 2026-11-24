# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Install-pipeline coverage for the FFmpeg checksum back-off (issue #302).

``download_and_install_bundled`` checks the marker before downloading,
writes it on ``checksum_mismatch`` and clears it on success.  Writing or
removing the marker never changes the install result.  The download,
verify, extract and verify_runs steps are mocked like in
``test_parts_check_atomic_extract.py``; no network is used.
"""

from __future__ import annotations

import json
import platform
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from workers.services.parts_check.ffmpeg_download_pkg import (
    checksum_backoff as backoff,
    cleanup as cleanup_mod,
    install as install_mod,
)
from workers.services.parts_check.ffmpeg_download_pkg.download import (
    ChecksumMismatchError,
    DownloadFailedError,
)

SUFFIX = ".zip" if platform.system() in ("Windows", "Darwin") else ".tar.xz"
URL = "https://example.invalid/ffmpeg" + SUFFIX
SHA = "a" * 64
ACTUAL = "b" * 64
T0 = datetime(2026, 11, 23, 10, 0, 0, tzinfo=timezone.utc)


def _binary_name() -> str:
    return "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg"


def _stage_archive(url, dest):
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Path(dest).write_bytes(b"archive-bytes")


def _stage_extract_with_binary(archive, dest):
    bin_dir = Path(dest) / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / _binary_name()).write_bytes(b"\x7fELF")


@pytest.fixture
def install_dir(tmp_path):
    return tmp_path / "data" / "bin" / "ffmpeg" / "8.1"


@pytest.fixture
def pipeline(install_dir, mocker):
    """Stub every external step; the defaults give a successful install."""
    tmp_archive = install_dir.with_name(install_dir.name + ".tmp" + SUFFIX)
    mocker.patch.object(
        install_mod, "_resolve_paths",
        return_value=(install_dir, tmp_archive, URL, SHA),
    )
    mocker.patch.object(install_mod, "get_platform_id", return_value="linux-x64")
    mocker.patch.object(install_mod, "_check_placeholders", return_value=True)
    mocker.patch.object(backoff, "_utcnow", return_value=T0)
    return {
        "download": mocker.patch.object(
            install_mod, "stream_download", side_effect=_stage_archive,
        ),
        "verify": mocker.patch.object(install_mod, "verify_sha256"),
        "extract": mocker.patch.object(
            install_mod, "extract_archive", side_effect=_stage_extract_with_binary,
        ),
        "runs": mocker.patch.object(install_mod, "verify_runs", return_value=True),
        "clock": backoff._utcnow,
    }


def _mismatch(pipeline):
    pipeline["verify"].side_effect = ChecksumMismatchError(
        "sha256 mismatch", expected=SHA, actual=ACTUAL,
    )


def _marker(install_dir):
    return backoff.marker_path(install_dir)


class TestMismatchWritesMarker:
    def test_mismatch_writes_the_marker(self, install_dir, pipeline):
        _mismatch(pipeline)

        status = install_mod.download_and_install_bundled(install_dir)

        assert status.status == "failed"
        assert status.error == "checksum_mismatch"
        data = json.loads(_marker(install_dir).read_text(encoding="utf-8"))
        assert data == {
            "url": URL,
            "expected_sha256": SHA,
            "actual_sha256": ACTUAL,
            "failed_at": T0.isoformat(),
        }

    def test_mismatch_without_digests_still_writes_marker(self, install_dir, pipeline):
        pipeline["verify"].side_effect = ChecksumMismatchError("mismatch")
        status = install_mod.download_and_install_bundled(install_dir)
        assert status.error == "checksum_mismatch"
        data = json.loads(_marker(install_dir).read_text(encoding="utf-8"))
        assert data["actual_sha256"] is None

    def test_next_call_skips_the_download(self, install_dir, pipeline):
        _mismatch(pipeline)
        install_mod.download_and_install_bundled(install_dir)
        pipeline["download"].reset_mock()
        pipeline["clock"].return_value = T0 + timedelta(hours=1)

        status = install_mod.download_and_install_bundled(install_dir)

        assert status.status == "failed"
        assert status.error == "checksum_mismatch"
        pipeline["download"].assert_not_called()
        assert _marker(install_dir).exists()

    def test_call_after_24h_downloads_again(self, install_dir, pipeline):
        _mismatch(pipeline)
        install_mod.download_and_install_bundled(install_dir)
        pipeline["download"].reset_mock()
        pipeline["clock"].return_value = T0 + timedelta(hours=24)

        status = install_mod.download_and_install_bundled(install_dir)

        assert status.error == "checksum_mismatch"
        pipeline["download"].assert_called_once()

    def test_download_failure_writes_no_marker(self, install_dir, pipeline):
        pipeline["download"].side_effect = DownloadFailedError("offline")
        status = install_mod.download_and_install_bundled(install_dir)
        assert status.error == "download_failed"
        assert not _marker(install_dir).exists()

    def test_marker_for_other_pins_does_not_skip(self, install_dir, pipeline):
        install_dir.parent.mkdir(parents=True)
        backoff.record_failure(install_dir, URL + ".old", SHA, ACTUAL, now=T0)

        status = install_mod.download_and_install_bundled(install_dir)

        assert status.status == "ready"
        pipeline["download"].assert_called_once()


class TestSuccessClearsMarker:
    def test_success_clears_a_stale_marker(self, install_dir, pipeline):
        install_dir.parent.mkdir(parents=True)
        backoff.record_failure(install_dir, URL, SHA, ACTUAL, now=T0 - timedelta(days=2))

        status = install_mod.download_and_install_bundled(install_dir)

        assert status.status == "ready"
        assert status.path == str(install_dir / "bin" / _binary_name())
        assert not _marker(install_dir).exists()

    def test_later_failure_after_mismatch_keeps_marker(self, install_dir, pipeline):
        """Only a full success removes the marker."""
        _mismatch(pipeline)
        install_mod.download_and_install_bundled(install_dir)
        pipeline["verify"].side_effect = None
        pipeline["runs"].return_value = False
        pipeline["clock"].return_value = T0 + timedelta(hours=25)

        status = install_mod.download_and_install_bundled(install_dir)

        assert status.error == "verify_runs_failed"
        assert _marker(install_dir).exists()


class TestMarkerErrorsDoNotChangeResult:
    def test_write_error_keeps_checksum_mismatch(self, install_dir, pipeline):
        install_dir.parent.mkdir(parents=True)
        _marker(install_dir).mkdir()  # the marker file cannot be written
        _mismatch(pipeline)

        status = install_mod.download_and_install_bundled(install_dir)

        assert status.status == "failed"
        assert status.error == "checksum_mismatch"
        assert _marker(install_dir).is_dir()
        assert sorted(p.name for p in install_dir.parent.iterdir()) == [
            _marker(install_dir).name,
        ]

    def test_write_oserror_keeps_checksum_mismatch(self, install_dir, pipeline, mocker):
        _mismatch(pipeline)
        mocker.patch.object(
            type(install_dir), "write_text", side_effect=OSError("disk full"),
        )

        status = install_mod.download_and_install_bundled(install_dir)

        assert status.error == "checksum_mismatch"
        assert not _marker(install_dir).exists()
        assert list(install_dir.parent.iterdir()) == []

    def test_marker_replace_failure_keeps_checksum_mismatch(
        self, install_dir, pipeline, mocker,
    ):
        """The atomic write's os.replace fails: no marker, no stray temp."""
        _mismatch(pipeline)
        mocker.patch.object(backoff.os, "replace", side_effect=OSError("no space"))

        status = install_mod.download_and_install_bundled(install_dir)

        assert status.status == "failed"
        assert status.error == "checksum_mismatch"
        assert list(install_dir.parent.iterdir()) == []

    def test_clear_oserror_keeps_ready(self, install_dir, pipeline, mocker):
        install_dir.parent.mkdir(parents=True)
        backoff.record_failure(install_dir, URL, SHA, ACTUAL, now=T0 - timedelta(days=2))
        real_unlink = Path.unlink

        def unlink(self, *args, **kwargs):
            if self.name.endswith(backoff.MARKER_SUFFIX):
                raise PermissionError("locked")
            return real_unlink(self, *args, **kwargs)

        mocker.patch.object(type(install_dir), "unlink", unlink)

        status = install_mod.download_and_install_bundled(install_dir)

        assert status.status == "ready"
        assert status.error is None
        assert _marker(install_dir).exists()


class TestStalePartialSweepKeepsMarker:
    def test_sweep_leaves_the_marker(self, install_dir):
        root = install_dir.parent
        root.mkdir(parents=True)
        backoff.record_failure(install_dir, URL, SHA, ACTUAL, now=T0)
        (root / "8.1.partial").mkdir()
        (root / "8.1.tmp.tar.xz").write_bytes(b"x")

        cleanup_mod.cleanup_stale_partials(root)

        assert _marker(install_dir).is_file()
        assert not (root / "8.1.partial").exists()
        assert not (root / "8.1.tmp.tar.xz").exists()

    def test_entry_cleanup_leaves_the_marker(self, install_dir, pipeline):
        """The tmp/partial cleanup at pipeline entry keeps the marker too."""
        install_dir.parent.mkdir(parents=True)
        backoff.record_failure(install_dir, URL + ".old", SHA, ACTUAL, now=T0)
        pipeline["download"].side_effect = DownloadFailedError("offline")

        install_mod.download_and_install_bundled(install_dir)

        assert _marker(install_dir).is_file()
