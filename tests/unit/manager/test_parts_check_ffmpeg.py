# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Smoke tests for the FFmpeg part check_fn.

Full coverage (download + extract + verify happy path, traversal
attempts, override file types) is the test-agent's domain.  These
tests cover the security-critical no-placeholder assertion plus a
couple of structural sanity checks.
"""

from workers.services.parts_check.ffmpeg_download_pkg import constants


def test_no_placeholder_sha_constants():
    """Spec §49: every pinned SHA-256 must be populated, real, hex."""
    for pid, digest in constants.FFMPEG_SHA256.items():
        assert digest, f"FFMPEG_SHA256[{pid!r}] is empty"
        assert digest != constants.PLACEHOLDER_SENTINEL, (
            f"FFMPEG_SHA256[{pid!r}] is placeholder sentinel"
        )
        # 64-char lowercase hex.
        assert len(digest) == 64
        int(digest, 16)  # raises ValueError if non-hex


def test_pinned_version_is_8_1():
    """Version constant is the spec-pinned value."""
    assert constants.FFMPEG_VERSION == "8.1"


def test_is_placeholder_helper():
    """is_placeholder catches None / empty / sentinel."""
    assert constants.is_placeholder(None)
    assert constants.is_placeholder("")
    assert constants.is_placeholder(constants.PLACEHOLDER_SENTINEL)
    assert not constants.is_placeholder("a" * 64)


def test_every_url_has_a_sha_constant():
    """Every supported platform has both a URL and a SHA-256 digest."""
    assert set(constants.FFMPEG_URLS.keys()) == set(
        constants.FFMPEG_SHA256.keys(),
    )
