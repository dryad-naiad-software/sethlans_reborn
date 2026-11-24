# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regression tests for the FFmpeg download pins (issue #302).

The BtbN ``latest`` release is rebuilt every day, so a SHA-256 pinned
against a ``latest/...`` URL goes stale and every fresh install fails
``checksum_mismatch``.  The pins must point at one dated, immutable
month-end ``autobuild-*`` release.

Also covers ``verify_sha256`` carrying the digests on
``ChecksumMismatchError``, which the checksum back-off marker records.
"""

from __future__ import annotations

import calendar
import hashlib
import re

import pytest

from workers.services.parts_check.ffmpeg_download_pkg import constants
from workers.services.parts_check.ffmpeg_download_pkg.download import (
    ChecksumMismatchError,
    verify_sha256,
)

BTBN_PREFIX = "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
TAG_RE = re.compile(r"^autobuild-(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
BTBN_PLATFORMS = {"windows-x64", "linux-x64", "linux-arm64"}


def _btbn_urls() -> dict[str, str]:
    return {
        pid: url for pid, url in constants.FFMPEG_URLS.items()
        if url.startswith(BTBN_PREFIX)
    }


class TestBtbnPins:
    def test_no_url_uses_the_moving_latest_release(self):
        for pid, url in constants.FFMPEG_URLS.items():
            assert "/releases/download/latest/" not in url, pid
            assert "/latest/" not in url, pid

    def test_btbn_platforms_are_exactly_the_three_expected(self):
        assert set(_btbn_urls()) == BTBN_PLATFORMS
        assert set(constants.BTBN_ASSETS) == BTBN_PLATFORMS

    def test_every_btbn_url_is_under_the_pinned_tag(self):
        base = BTBN_PREFIX + constants.BTBN_RELEASE_TAG + "/"
        assert constants.BTBN_DOWNLOAD_BASE == base
        for pid, url in _btbn_urls().items():
            assert url == base + constants.BTBN_ASSETS[pid]

    def test_release_tag_has_the_dated_autobuild_shape(self):
        assert TAG_RE.match(constants.BTBN_RELEASE_TAG), (
            constants.BTBN_RELEASE_TAG
        )

    def test_release_tag_is_a_month_end_build(self):
        """Month-end builds are the ones BtbN keeps for about two years."""
        year, month, day = (
            int(g) for g in TAG_RE.match(constants.BTBN_RELEASE_TAG).groups()[:3]
        )
        assert day == calendar.monthrange(year, month)[1]

    @pytest.mark.parametrize("pid", sorted(BTBN_PLATFORMS))
    def test_asset_is_the_pinned_version_gpl_build(self, pid):
        asset = constants.BTBN_ASSETS[pid]
        assert "/" not in asset
        assert asset.startswith(f"ffmpeg-n{constants.FFMPEG_VERSION}")
        assert f"-gpl-{constants.FFMPEG_VERSION}." in asset

    def test_archive_types_match_what_the_extractor_dispatches_on(self):
        assert constants.FFMPEG_URLS["windows-x64"].endswith(".zip")
        assert constants.FFMPEG_URLS["linux-x64"].endswith(".tar.xz")
        assert constants.FFMPEG_URLS["linux-arm64"].endswith(".tar.xz")


class TestPinTables:
    def test_url_and_digest_keys_match(self):
        assert set(constants.FFMPEG_URLS) == set(constants.FFMPEG_SHA256)

    def test_every_digest_is_64_lowercase_hex(self):
        for pid, digest in constants.FFMPEG_SHA256.items():
            assert isinstance(digest, str), pid
            assert DIGEST_RE.match(digest), (pid, digest)

    def test_every_url_is_https(self):
        for pid, url in constants.FFMPEG_URLS.items():
            assert url.startswith("https://"), pid

    def test_platforms_sharing_a_url_share_a_digest(self):
        by_url: dict[str, set[str]] = {}
        for pid, url in constants.FFMPEG_URLS.items():
            by_url.setdefault(url, set()).add(constants.FFMPEG_SHA256[pid])
        for url, digests in by_url.items():
            assert len(digests) == 1, url

    def test_the_stale_latest_digests_are_gone(self):
        """The 2026-04-30 ``latest`` digests from the bug report."""
        stale = {
            "3f513a4ac4bc9493e81ba18cc0d752da8a4fbf4ae098372dc576c26aafe81ba9",
            "cadf9d157e199a132e9e032d6e7d1c94fbae3f1caa5885bc7595ea06b55f56ed",
            "db3bd15b5a32e54ddefe9830b82247f3f0b068aca6f399ff6815754c8748d9ea",
        }
        assert stale.isdisjoint(constants.FFMPEG_SHA256.values())


class TestVerifySha256Mismatch:
    def test_mismatch_carries_lowercase_expected_and_actual(self, tmp_path):
        payload = tmp_path / "archive.bin"
        payload.write_bytes(b"not the pinned bytes")
        actual = hashlib.sha256(b"not the pinned bytes").hexdigest()
        expected = "AB" * 32

        with pytest.raises(ChecksumMismatchError) as info:
            verify_sha256(payload, expected)

        assert info.value.expected == "ab" * 32
        assert info.value.actual == actual
        assert str(info.value) == "sha256 mismatch"

    def test_match_is_case_insensitive_and_returns_none(self, tmp_path):
        payload = tmp_path / "archive.bin"
        payload.write_bytes(b"pinned")
        digest = hashlib.sha256(b"pinned").hexdigest().upper()
        assert verify_sha256(payload, digest) is None

    def test_error_defaults_when_digests_unknown(self):
        exc = ChecksumMismatchError()
        assert str(exc) == "sha256 mismatch"
        assert exc.expected is None
        assert exc.actual is None
