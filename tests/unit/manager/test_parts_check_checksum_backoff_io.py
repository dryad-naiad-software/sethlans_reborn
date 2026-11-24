# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read/write hardening of the FFmpeg checksum back-off marker (#302).

The marker is read only when ``lstat`` shows a small regular file
(at most ``MAX_MARKER_BYTES``), any read or parse error fails open to a
normal download, and it is written atomically (temp file in the same
folder + ``os.replace``), so a symlink at the marker path is replaced,
not written through.  Window and pin matching are covered in
``test_parts_check_checksum_backoff.py``.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from workers.services.parts_check.ffmpeg_download_pkg import (
    checksum_backoff as backoff,
)

URL = "https://example.invalid/ffmpeg-8.1.tar.xz"
SHA = "c" * 64
ACTUAL = "d" * 64
T0 = datetime(2026, 11, 23, 10, 0, 0, tzinfo=timezone.utc)
SOON = T0 + timedelta(minutes=1)


@pytest.fixture
def install_dir(tmp_path):
    root = tmp_path / "bin" / "ffmpeg"
    root.mkdir(parents=True)
    return root / "8.1"


def _marker_json(pad_to=None) -> bytes:
    raw = json.dumps({
        "url": URL, "expected_sha256": SHA,
        "actual_sha256": ACTUAL, "failed_at": T0.isoformat(),
    }).encode("utf-8")
    if pad_to is not None:
        raw += b" " * (pad_to - len(raw))  # trailing whitespace is valid JSON
        assert len(raw) == pad_to
    return raw


def _skip(install_dir):
    return backoff.should_skip_download(install_dir, URL, SHA, now=SOON)


def _symlink_or_skip(target, link):
    """Create a symlink; skip only on Windows without symlink rights.

    On POSIX a failure is a test error, so Linux CI always runs these.
    """
    if sys.platform != "win32":
        os.symlink(target, link)
        return
    try:
        os.symlink(target, link)
    except OSError as exc:
        pytest.skip(f"cannot create symlinks on this Windows account: {exc}")


class TestReadLimits:
    def test_max_marker_bytes_is_4096(self):
        assert backoff.MAX_MARKER_BYTES == 4096

    def test_marker_of_exactly_max_bytes_is_read(self, install_dir):
        backoff.marker_path(install_dir).write_bytes(_marker_json(pad_to=4096))
        assert _skip(install_dir) is True

    @pytest.mark.parametrize("size", [4097, 64 * 1024])
    def test_oversized_marker_downloads(self, install_dir, size):
        backoff.marker_path(install_dir).write_bytes(_marker_json(pad_to=size))
        assert _skip(install_dir) is False

    @pytest.mark.parametrize("depth", [4096, 100000])
    def test_deeply_nested_json_downloads(self, install_dir, depth):
        backoff.marker_path(install_dir).write_text("[" * depth, encoding="ascii")
        assert _skip(install_dir) is False

    def test_recursion_error_while_parsing_downloads(self, install_dir, mocker):
        """Small nested JSON may still blow the parser's recursion limit."""
        backoff.marker_path(install_dir).write_bytes(_marker_json())
        mocker.patch.object(backoff.json, "loads", side_effect=RecursionError("deep"))
        assert _skip(install_dir) is False

    def test_directory_at_marker_path_downloads(self, install_dir):
        backoff.marker_path(install_dir).mkdir()
        assert _skip(install_dir) is False

    def test_fstat_recheck_rejects_a_changed_entry(self, install_dir, mocker):
        backoff.marker_path(install_dir).write_bytes(_marker_json())
        mocker.patch.object(
            backoff.os, "fstat",
            return_value=SimpleNamespace(st_mode=stat.S_IFREG, st_size=4097),
        )
        assert _skip(install_dir) is False

    def test_unexpected_error_in_the_check_downloads(self, install_dir, mocker):
        mocker.patch.object(backoff, "_read_marker", side_effect=RuntimeError("boom"))
        assert _skip(install_dir) is False

    def test_symlink_marker_is_not_followed(self, install_dir, tmp_path):
        target = tmp_path / "elsewhere.json"
        target.write_bytes(_marker_json())
        _symlink_or_skip(target, backoff.marker_path(install_dir))
        assert _skip(install_dir) is False

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this OS")
    def test_fifo_marker_downloads_without_blocking(self, install_dir):
        os.mkfifo(backoff.marker_path(install_dir))
        assert _skip(install_dir) is False


class TestAtomicWrite:
    def _siblings(self, install_dir):
        return sorted(p.name for p in install_dir.parent.iterdir())

    def test_success_leaves_only_the_marker(self, install_dir):
        backoff.record_failure(install_dir, URL, SHA, ACTUAL, now=T0)
        assert self._siblings(install_dir) == ["8.1.checksum-failed"]

    def test_rewrite_replaces_an_existing_marker(self, install_dir):
        backoff.record_failure(install_dir, URL, SHA, ACTUAL, now=T0 - timedelta(days=3))
        backoff.record_failure(install_dir, URL, SHA, ACTUAL, now=T0)
        assert self._siblings(install_dir) == ["8.1.checksum-failed"]
        assert _skip(install_dir) is True

    def test_temp_is_written_beside_the_marker_and_swept_as_tmp(self, install_dir, mocker):
        spy = mocker.patch.object(backoff.os, "replace", side_effect=os.replace)
        backoff.record_failure(install_dir, URL, SHA, ACTUAL, now=T0)
        src, dst = (os.fspath(a) for a in spy.call_args.args)
        assert os.path.dirname(src) == str(install_dir.parent)
        assert os.path.basename(src).startswith("8.1.checksum-failed.")
        assert src.endswith(".tmp")
        assert dst == str(backoff.marker_path(install_dir))

    def test_replace_failure_removes_the_temp(self, install_dir, mocker):
        mocker.patch.object(backoff.os, "replace", side_effect=OSError("no space"))
        backoff.record_failure(install_dir, URL, SHA, ACTUAL, now=T0)
        assert self._siblings(install_dir) == []

    def test_temp_write_failure_removes_the_temp(self, install_dir, mocker):
        mocker.patch.object(
            type(install_dir), "write_text", side_effect=OSError("disk full"),
        )
        backoff.record_failure(install_dir, URL, SHA, ACTUAL, now=T0)
        assert self._siblings(install_dir) == []

    def test_mkstemp_failure_is_swallowed(self, install_dir, mocker):
        mocker.patch.object(
            backoff.tempfile, "mkstemp", side_effect=PermissionError("read-only"),
        )
        backoff.record_failure(install_dir, URL, SHA, ACTUAL, now=T0)
        assert self._siblings(install_dir) == []

    def test_planted_symlink_is_replaced_not_written_through(self, install_dir, tmp_path):
        target = tmp_path / "victim.txt"
        target.write_bytes(b"do not touch")
        marker = backoff.marker_path(install_dir)
        _symlink_or_skip(target, marker)

        backoff.record_failure(install_dir, URL, SHA, ACTUAL, now=T0)

        assert target.read_bytes() == b"do not touch"
        assert not marker.is_symlink()
        assert marker.is_file()
        assert json.loads(marker.read_text(encoding="utf-8"))["url"] == URL
