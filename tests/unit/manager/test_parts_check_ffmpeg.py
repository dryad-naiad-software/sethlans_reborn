# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Tests for the FFmpeg part check_fn.

Covers the substantive ``check_ffmpeg()`` flow per spec FR §34-39:
PATH detection happy path, version-gate fall-through, manager.ini
override happy/invalid/unverifiable, fixed-dir presence check
(re-boot fast path), fall-through to the bundled-download pipeline,
and closed-vocabulary error string assertions.
"""

from pathlib import Path

import pytest

from workers.services.parts_check import ffmpeg as ffmpeg_part
from workers.services.parts_check import registry
from workers.services.parts_check.ffmpeg_download_pkg import constants


# ---- SHA / placeholder constants tests (preserved from skeleton) -----

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


# ---- check_ffmpeg() flow tests ---------------------------------------

@pytest.fixture
def isolate_ffmpeg(tmp_path, mocker):
    """Isolate check_ffmpeg from real filesystem and config."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    mocker.patch.object(ffmpeg_part, "get_data_dir", return_value=data_dir)
    override_reader = mocker.patch.object(
        ffmpeg_part, "_read_manager_ini_override", return_value=None,
    )
    cleanup = mocker.patch.object(ffmpeg_part, "cleanup_stale_partials")
    return {
        "data_dir": data_dir,
        "override_reader": override_reader,
        "cleanup_stale_partials": cleanup,
    }


def test_check_ffmpeg_path_lookup_happy_returns_ready_system(
    isolate_ffmpeg, mocker,
):
    """PATH ffmpeg with major >= 8 -> ready, source=system."""
    mocker.patch(
        "workers.services.parts_check.ffmpeg.shutil.which",
        return_value="/usr/bin/ffmpeg",
    )
    mocker.patch.object(ffmpeg_part, "verify_runs", return_value=True)
    mocker.patch.object(
        ffmpeg_part, "parse_major_version", return_value=8,
    )

    status = ffmpeg_part.check_ffmpeg()

    assert status.status == "ready"
    assert status.source == "system"
    assert status.path == "/usr/bin/ffmpeg"
    assert status.version == "8"
    assert status.error is None


def test_check_ffmpeg_path_version_below_8_falls_through(
    isolate_ffmpeg, mocker,
):
    """PATH ffmpeg major < 8 -> falls through (NOT a failure)."""
    mocker.patch(
        "workers.services.parts_check.ffmpeg.shutil.which",
        return_value="/usr/bin/ffmpeg",
    )
    mocker.patch.object(ffmpeg_part, "verify_runs", return_value=True)
    mocker.patch.object(
        ffmpeg_part, "parse_major_version", return_value=7,
    )
    mocker.patch.object(
        ffmpeg_part, "get_ffmpeg_binary", return_value=None,
    )
    install_status = registry.Status(
        status="ready", source="bundled",
        version="8.1", path="/bundled/ffmpeg",
    )
    download = mocker.patch.object(
        ffmpeg_part, "download_and_install_bundled",
        return_value=install_status,
    )

    status = ffmpeg_part.check_ffmpeg()

    download.assert_called_once()
    assert status.status == "ready"
    assert status.source == "bundled"


def test_check_ffmpeg_override_happy_returns_ready_system(
    isolate_ffmpeg, mocker, tmp_path,
):
    """manager.ini override pointing to valid binary -> ready/system."""
    fake_binary = tmp_path / "ff" / "ffmpeg"
    fake_binary.parent.mkdir()
    fake_binary.write_bytes(b"\x7fELF")
    isolate_ffmpeg["override_reader"].return_value = str(fake_binary)

    mocker.patch.object(ffmpeg_part, "verify_runs", return_value=True)
    mocker.patch.object(
        ffmpeg_part, "parse_major_version", return_value=8,
    )

    status = ffmpeg_part.check_ffmpeg()

    assert status.status == "ready"
    assert status.source == "system"
    # Realpath resolution applied.
    import os as _os
    assert status.path == _os.path.realpath(str(fake_binary))
    assert status.error is None


@pytest.mark.parametrize("override_kind", ["nonexistent", "directory"])
def test_check_ffmpeg_override_invalid(
    isolate_ffmpeg, tmp_path, override_kind,
):
    """Override pointing to a missing path or non-regular file -> invalid."""
    if override_kind == "nonexistent":
        isolate_ffmpeg["override_reader"].return_value = (
            "/nonexistent/path/to/ffmpeg-binary-xyz"
        )
    else:
        a_dir = tmp_path / "not_a_file"
        a_dir.mkdir()
        isolate_ffmpeg["override_reader"].return_value = str(a_dir)

    status = ffmpeg_part.check_ffmpeg()
    assert status.status == "failed"
    assert status.error == "override_path_invalid"


@pytest.mark.parametrize(
    "verify_runs_result,major",
    [(False, 8), (True, 7), (True, None)],
    ids=["verify_runs_fails", "version_below_8", "version_unparseable"],
)
def test_check_ffmpeg_override_unverifiable(
    isolate_ffmpeg, mocker, tmp_path, verify_runs_result, major,
):
    """Override fails verify_runs OR major < 8 OR unparseable -> unverifiable."""
    fake_binary = tmp_path / "ffmpeg"
    fake_binary.write_bytes(b"\x7fELF")
    isolate_ffmpeg["override_reader"].return_value = str(fake_binary)

    mocker.patch.object(
        ffmpeg_part, "verify_runs", return_value=verify_runs_result,
    )
    mocker.patch.object(
        ffmpeg_part, "parse_major_version", return_value=major,
    )

    status = ffmpeg_part.check_ffmpeg()
    assert status.status == "failed"
    assert status.error == "override_path_unverifiable"


def test_check_ffmpeg_bundled_present_fast_path_ready_bundled(
    isolate_ffmpeg, mocker,
):
    """Bundled binary present + verify_runs ok -> ready/bundled, no DL."""
    bundled_path = Path("/data/bin/ffmpeg/8.1/ffmpeg")
    mocker.patch(
        "workers.services.parts_check.ffmpeg.shutil.which",
        return_value=None,
    )
    mocker.patch.object(
        ffmpeg_part, "get_ffmpeg_binary", return_value=bundled_path,
    )
    mocker.patch.object(ffmpeg_part, "verify_runs", return_value=True)
    download = mocker.patch.object(
        ffmpeg_part, "download_and_install_bundled",
    )

    status = ffmpeg_part.check_ffmpeg()

    download.assert_not_called()
    assert status.status == "ready"
    assert status.source == "bundled"
    assert status.version == "8.1"
    assert status.path == str(bundled_path)
    assert status.error is None


def test_check_ffmpeg_bundled_present_but_verify_fails_falls_through(
    isolate_ffmpeg, mocker,
):
    """Bundled binary present but stale (verify_runs False) -> redownload."""
    bundled_path = Path("/data/bin/ffmpeg/8.1/ffmpeg")
    mocker.patch(
        "workers.services.parts_check.ffmpeg.shutil.which",
        return_value=None,
    )
    mocker.patch.object(
        ffmpeg_part, "get_ffmpeg_binary", return_value=bundled_path,
    )
    mocker.patch.object(ffmpeg_part, "verify_runs", return_value=False)
    install_status = registry.Status(
        status="ready", source="bundled",
        version="8.1", path="/new/ffmpeg",
    )
    download = mocker.patch.object(
        ffmpeg_part, "download_and_install_bundled",
        return_value=install_status,
    )

    status = ffmpeg_part.check_ffmpeg()

    download.assert_called_once()
    assert status == install_status


def test_check_ffmpeg_falls_through_to_download_no_path_no_bundled(
    isolate_ffmpeg, mocker,
):
    """No PATH, no bundled present -> download is invoked."""
    mocker.patch(
        "workers.services.parts_check.ffmpeg.shutil.which",
        return_value=None,
    )
    mocker.patch.object(
        ffmpeg_part, "get_ffmpeg_binary", return_value=None,
    )
    install_status = registry.Status(
        status="ready", source="bundled",
        version="8.1", path="/x/ffmpeg",
    )
    download = mocker.patch.object(
        ffmpeg_part, "download_and_install_bundled",
        return_value=install_status,
    )

    status = ffmpeg_part.check_ffmpeg()

    download.assert_called_once()
    assert status.status == "ready"
    assert status.source == "bundled"


def test_check_ffmpeg_calls_cleanup_stale_partials_first(
    isolate_ffmpeg, mocker,
):
    """check_ffmpeg always invokes the stale-partials sweep on entry."""
    mocker.patch(
        "workers.services.parts_check.ffmpeg.shutil.which",
        return_value="/usr/bin/ffmpeg",
    )
    mocker.patch.object(ffmpeg_part, "verify_runs", return_value=True)
    mocker.patch.object(
        ffmpeg_part, "parse_major_version", return_value=8,
    )

    ffmpeg_part.check_ffmpeg()

    isolate_ffmpeg["cleanup_stale_partials"].assert_called_once()


# Re-entrancy and closed-vocabulary error tests live in
# tests/unit/manager/test_parts_check_ffmpeg_errors.py.
# Placeholder-SHA hard-fail tests live in
# tests/unit/manager/test_parts_check_atomic_extract.py.
