# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests for the FFmpeg checksum back-off marker (issue #302).

After a checksum mismatch the install pipeline writes
``<install_dir>.checksum-failed``.  For 24 h a marker for the same URL
and digest makes the boot parts check skip the ~150 MB download.  Any
doubt about the marker fails open to a normal download.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from workers.services.parts_check.ffmpeg_download_pkg import (
    checksum_backoff as backoff,
)

URL = "https://example.invalid/ffmpeg-8.1.tar.xz"
SHA = "c" * 64
ACTUAL = "d" * 64
T0 = datetime(2026, 11, 23, 10, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def install_dir(tmp_path):
    root = tmp_path / "bin" / "ffmpeg"
    root.mkdir(parents=True)
    return root / "8.1"


def _write_marker(install_dir, **fields):
    data = {
        "url": URL,
        "expected_sha256": SHA,
        "actual_sha256": ACTUAL,
        "failed_at": T0.isoformat(),
    }
    data.update(fields)
    backoff.marker_path(install_dir).write_text(json.dumps(data), encoding="utf-8")


def _skip(install_dir, now, url=URL, sha=SHA):
    return backoff.should_skip_download(install_dir, url, sha, now=now)


class TestMarkerPath:
    def test_marker_is_a_sibling_of_the_install_dir(self, install_dir):
        path = backoff.marker_path(install_dir)
        assert path.parent == install_dir.parent
        assert path.name == "8.1.checksum-failed"


class TestRecordFailure:
    def test_writes_all_fields(self, install_dir):
        backoff.record_failure(install_dir, URL, SHA.upper(), ACTUAL.upper(), now=T0)

        data = json.loads(backoff.marker_path(install_dir).read_text(encoding="utf-8"))
        assert data == {
            "url": URL,
            "expected_sha256": SHA,
            "actual_sha256": ACTUAL,
            "failed_at": "2026-11-23T10:00:00+00:00",
        }

    def test_unknown_actual_digest_is_null(self, install_dir):
        backoff.record_failure(install_dir, URL, SHA, None, now=T0)
        data = json.loads(backoff.marker_path(install_dir).read_text(encoding="utf-8"))
        assert data["actual_sha256"] is None

    def test_failed_at_is_normalised_to_utc(self, install_dir):
        plus_two = timezone(timedelta(hours=2))
        backoff.record_failure(
            install_dir, URL, SHA, ACTUAL, now=T0.astimezone(plus_two),
        )
        data = json.loads(backoff.marker_path(install_dir).read_text(encoding="utf-8"))
        assert data["failed_at"] == "2026-11-23T10:00:00+00:00"

    def test_default_clock_is_utcnow(self, install_dir, mocker):
        mocker.patch.object(backoff, "_utcnow", return_value=T0)
        backoff.record_failure(install_dir, URL, SHA, ACTUAL)
        data = json.loads(backoff.marker_path(install_dir).read_text(encoding="utf-8"))
        assert data["failed_at"] == T0.isoformat()

    def test_write_error_is_swallowed(self, install_dir):
        # A directory where the marker file should be makes the write fail.
        backoff.marker_path(install_dir).mkdir()
        backoff.record_failure(install_dir, URL, SHA, ACTUAL, now=T0)
        assert backoff.marker_path(install_dir).is_dir()

    def test_recorded_marker_round_trips_to_a_skip(self, install_dir):
        backoff.record_failure(install_dir, URL, SHA, ACTUAL, now=T0)
        assert _skip(install_dir, T0 + timedelta(minutes=1)) is True


class TestClear:
    def test_removes_the_marker(self, install_dir):
        _write_marker(install_dir)
        backoff.clear(install_dir)
        assert not backoff.marker_path(install_dir).exists()

    def test_missing_marker_is_fine(self, install_dir):
        backoff.clear(install_dir)
        assert not backoff.marker_path(install_dir).exists()

    def test_remove_error_is_swallowed(self, install_dir, mocker):
        _write_marker(install_dir)
        mocker.patch.object(
            type(install_dir), "unlink", side_effect=PermissionError("locked"),
        )
        backoff.clear(install_dir)
        assert backoff.marker_path(install_dir).exists()


class TestWindow:
    def test_no_marker_downloads(self, install_dir):
        assert _skip(install_dir, T0) is False

    @pytest.mark.parametrize("age", [
        timedelta(0),
        timedelta(seconds=1),
        timedelta(hours=23, minutes=59, seconds=59),
    ], ids=["same_instant", "one_second", "just_under_24h"])
    def test_skips_within_24_hours(self, install_dir, age):
        _write_marker(install_dir)
        assert _skip(install_dir, T0 + age) is True

    @pytest.mark.parametrize("age", [
        timedelta(hours=24),
        timedelta(hours=24, microseconds=1),
        timedelta(days=30),
    ], ids=["exactly_24h", "just_over_24h", "thirty_days"])
    def test_downloads_at_24_hours_and_later(self, install_dir, age):
        _write_marker(install_dir)
        assert _skip(install_dir, T0 + age) is False

    def test_future_failed_at_downloads(self, install_dir):
        _write_marker(install_dir)
        assert _skip(install_dir, T0 - timedelta(seconds=1)) is False

    def test_next_attempt_time_is_failed_at_plus_backoff(self, install_dir):
        _write_marker(install_dir)
        when = backoff.next_attempt_time(
            install_dir, URL, SHA, now=T0 + timedelta(hours=1),
        )
        assert when == T0 + timedelta(hours=24)
        assert backoff.BACKOFF == timedelta(hours=24)

    def test_other_offset_failed_at_is_compared_as_an_instant(self, install_dir):
        plus_five = timezone(timedelta(hours=5))
        _write_marker(install_dir, failed_at=T0.astimezone(plus_five).isoformat())
        assert _skip(install_dir, T0 + timedelta(hours=23)) is True
        assert _skip(install_dir, T0 + timedelta(hours=24)) is False

    def test_default_clock_is_utcnow(self, install_dir, mocker):
        _write_marker(install_dir)
        clock = mocker.patch.object(backoff, "_utcnow")
        clock.return_value = T0 + timedelta(hours=1)
        assert backoff.should_skip_download(install_dir, URL, SHA) is True
        clock.return_value = T0 + timedelta(hours=25)
        assert backoff.should_skip_download(install_dir, URL, SHA) is False

    def test_utcnow_is_aware_utc(self):
        now = backoff._utcnow()
        assert now.tzinfo is not None
        assert now.utcoffset() == timedelta(0)


class TestPinMatching:
    def test_different_url_downloads(self, install_dir):
        _write_marker(install_dir)
        assert _skip(install_dir, T0, url=URL + "?v=2") is False

    def test_url_compare_is_exact(self, install_dir):
        _write_marker(install_dir)
        assert _skip(install_dir, T0, url=URL.upper()) is False

    def test_different_digest_downloads(self, install_dir):
        _write_marker(install_dir)
        assert _skip(install_dir, T0, sha="e" * 64) is False

    def test_digest_compare_is_case_insensitive(self, install_dir):
        _write_marker(install_dir, expected_sha256=SHA.upper())
        assert _skip(install_dir, T0, sha=SHA) is True
        _write_marker(install_dir)
        assert _skip(install_dir, T0, sha=SHA.upper()) is True


class TestBadMarkersFailOpen:
    @pytest.mark.parametrize("raw", [
        "", "{not json", "[1, 2, 3]", "\"a string\"", "42", "null",
    ], ids=["empty", "malformed", "list", "string", "number", "null"])
    def test_unusable_json_downloads(self, install_dir, raw):
        backoff.marker_path(install_dir).write_text(raw, encoding="utf-8")
        assert _skip(install_dir, T0) is False

    def test_undecodable_bytes_download(self, install_dir):
        backoff.marker_path(install_dir).write_bytes(b"\xff\xfe\x00garbage")
        assert _skip(install_dir, T0) is False

    def test_unreadable_marker_downloads(self, install_dir):
        backoff.marker_path(install_dir).mkdir()
        assert _skip(install_dir, T0) is False

    @pytest.mark.parametrize("fields", [
        {"url": None},
        {"url": 7},
        {"expected_sha256": None},
        {"expected_sha256": ["c" * 64]},
        {"failed_at": None},
        {"failed_at": 1700000000},
        {"failed_at": "yesterday"},
        {"failed_at": ""},
    ], ids=[
        "url_null", "url_int", "sha_null", "sha_list",
        "time_null", "time_int", "time_text", "time_empty",
    ])
    def test_wrong_types_download(self, install_dir, fields):
        _write_marker(install_dir, **fields)
        assert _skip(install_dir, T0 + timedelta(minutes=1)) is False

    @pytest.mark.parametrize("missing", ["url", "expected_sha256", "failed_at"])
    def test_missing_field_downloads(self, install_dir, missing):
        data = {
            "url": URL, "expected_sha256": SHA, "failed_at": T0.isoformat(),
        }
        del data[missing]
        backoff.marker_path(install_dir).write_text(json.dumps(data), encoding="utf-8")
        assert _skip(install_dir, T0 + timedelta(minutes=1)) is False

    def test_timezone_less_failed_at_downloads(self, install_dir):
        _write_marker(install_dir, failed_at="2026-11-23T10:00:00")
        assert _skip(install_dir, T0 + timedelta(minutes=1)) is False

    def test_bad_actual_digest_does_not_matter(self, install_dir):
        """``actual_sha256`` is information only; it never gates the skip."""
        _write_marker(install_dir, actual_sha256=12345)
        assert _skip(install_dir, T0 + timedelta(minutes=1)) is True
