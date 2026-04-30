# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Atomic-install pipeline coverage (FR §52-58, Tests §414).

Covers the orchestrator: tmp/partial cleanup on every failure path,
os.replace promotion only after verify_runs, stale-partial sweep on
entry, closed-vocabulary error strings, placeholder-SHA hard-fail.

Extractor traversal-safety tests live in
``test_parts_check_extract_traversal.py``.
"""

from __future__ import annotations

import platform
from pathlib import Path
from unittest.mock import patch

import pytest

from workers.services.parts_check.ffmpeg_download_pkg import (
    cleanup as cleanup_mod,
    extract as extract_mod,
    install as install_mod,
    constants,
)
from workers.services.parts_check.ffmpeg_download_pkg.download import (
    ChecksumMismatchError,
    DownloadFailedError,
)


CLOSED_VOCAB_ERRORS = {
    "download_failed",
    "checksum_mismatch",
    "extraction_unsafe",
    "extraction_failed",
    "verify_runs_failed",
    "version_below_8",
    "override_path_invalid",
    "override_path_unverifiable",
    "placeholder_sha",
}


def _binary_name() -> str:
    return "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg"


def _platform_uses_zip() -> bool:
    return platform.system() in ("Windows", "Darwin")


@pytest.fixture
def install_dir(tmp_path):
    """Provide a typical ``<data_dir>/bin/ffmpeg/8.1`` path."""
    return tmp_path / "data" / "bin" / "ffmpeg" / "8.1"


def _patch_resolve_paths(install_dir, mocker):
    """Stub _resolve_paths so the install pipeline uses our paths."""
    suffix = ".zip" if _platform_uses_zip() else ".tar.xz"
    final_dir = install_dir
    tmp_archive = install_dir.with_name(install_dir.name + ".tmp" + suffix)
    url = "https://example.invalid/ffmpeg" + suffix
    sha = "a" * 64
    mocker.patch.object(
        install_mod, "_resolve_paths",
        return_value=(final_dir, tmp_archive, url, sha),
    )
    mocker.patch.object(
        install_mod, "get_platform_id", return_value="linux-x64",
    )
    mocker.patch.object(install_mod, "_check_placeholders", return_value=True)
    return final_dir, tmp_archive


def _stage_archive(url, dest):
    """Stand-in for stream_download: writes a small file."""
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Path(dest).write_bytes(b"archive-bytes")


def _stage_extract_with_binary(archive, dest):
    """Stand-in for extract_archive: produces a binary inside dest."""
    bin_dir = Path(dest) / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / _binary_name()).write_bytes(b"\x7fELF")


# ---- Failure paths: tmp + partial cleaned up, no os.replace ----------

@pytest.mark.parametrize(
    "stage,expected_error",
    [
        ("download", "download_failed"),
        ("checksum", "checksum_mismatch"),
        ("extract_unsafe", "extraction_unsafe"),
        ("extract_failed", "extraction_failed"),
        ("extract_unexpected", "extraction_failed"),
        ("verify_runs", "verify_runs_failed"),
    ],
)
def test_failure_at_each_stage_cleans_tmp_partial_and_skips_replace(
    install_dir, mocker, stage, expected_error,
):
    """Every failure stage leaves no tmp/partial on disk and skips replace."""
    final_dir, tmp_archive = _patch_resolve_paths(install_dir, mocker)
    partial_dir = final_dir.with_name(final_dir.name + ".partial")

    # Default stubs (overridden per-stage below).
    mocker.patch.object(install_mod, "stream_download", side_effect=_stage_archive)
    mocker.patch.object(install_mod, "verify_sha256")
    mocker.patch.object(
        install_mod, "extract_archive", side_effect=_stage_extract_with_binary,
    )
    mocker.patch.object(install_mod, "verify_runs", return_value=True)

    if stage == "download":
        # Pre-populate partial to ensure cleanup catches it (defensive).
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        partial_dir.mkdir()
        (partial_dir / "leftover.txt").write_text("stale")
        tmp_archive.write_bytes(b"junk")
        mocker.patch.object(
            install_mod, "stream_download",
            side_effect=DownloadFailedError("fake"),
        )
    elif stage == "checksum":
        mocker.patch.object(
            install_mod, "verify_sha256",
            side_effect=ChecksumMismatchError("mismatch"),
        )
    elif stage == "extract_unsafe":
        mocker.patch.object(
            install_mod, "extract_archive",
            side_effect=extract_mod.ExtractionUnsafeError("traversal"),
        )
    elif stage == "extract_failed":
        mocker.patch.object(
            install_mod, "extract_archive",
            side_effect=extract_mod.ExtractionFailedError("corrupt"),
        )
    elif stage == "extract_unexpected":
        mocker.patch.object(
            install_mod, "extract_archive",
            side_effect=RuntimeError("unexpected"),
        )
    elif stage == "verify_runs":
        mocker.patch.object(install_mod, "verify_runs", return_value=False)

    replace_spy = mocker.patch.object(install_mod.os, "replace")

    status = install_mod.download_and_install_bundled(install_dir)

    assert status.status == "failed"
    assert status.error == expected_error
    assert status.error in CLOSED_VOCAB_ERRORS
    # Free-form leak guard: no slashes (paths), no spaces (prose).
    assert "/" not in status.error
    assert " " not in status.error
    assert not tmp_archive.exists()
    assert not partial_dir.exists()
    replace_spy.assert_not_called()


# ---- Success path: os.replace promotes only after verify_runs --------

def test_success_path_uses_os_replace_after_verify_runs(install_dir, mocker):
    """Happy path -> os.replace called once, .tmp deleted, status ready."""
    final_dir, tmp_archive = _patch_resolve_paths(install_dir, mocker)
    partial_dir = final_dir.with_name(final_dir.name + ".partial")

    mocker.patch.object(install_mod, "stream_download", side_effect=_stage_archive)
    mocker.patch.object(install_mod, "verify_sha256")
    mocker.patch.object(
        install_mod, "extract_archive", side_effect=_stage_extract_with_binary,
    )
    mocker.patch.object(install_mod, "verify_runs", return_value=True)

    # Use real os.replace so we can assert post-conditions on disk.
    status = install_mod.download_and_install_bundled(install_dir)

    assert status.status == "ready"
    assert status.source == "bundled"
    assert status.version == constants.FFMPEG_VERSION
    assert status.error is None
    assert not tmp_archive.exists()
    assert not partial_dir.exists()
    assert final_dir.exists()
    assert (final_dir / "bin" / _binary_name()).exists()


def test_os_replace_not_called_until_verify_runs_passes(install_dir, mocker):
    """Side-effect ordering: verify_runs must precede os.replace."""
    _patch_resolve_paths(install_dir, mocker)

    call_order: list[str] = []

    def fake_download(url, dest):
        call_order.append("download")
        _stage_archive(url, dest)

    def fake_extract(archive, dest):
        call_order.append("extract")
        _stage_extract_with_binary(archive, dest)

    def fake_verify_runs(binary):
        call_order.append("verify_runs")
        return True

    real_replace = install_mod.os.replace

    def spy_replace(src, dst):
        call_order.append("os.replace")
        return real_replace(src, dst)

    mocker.patch.object(install_mod, "stream_download", side_effect=fake_download)
    mocker.patch.object(install_mod, "verify_sha256")
    mocker.patch.object(install_mod, "extract_archive", side_effect=fake_extract)
    mocker.patch.object(install_mod, "verify_runs", side_effect=fake_verify_runs)
    mocker.patch.object(install_mod.os, "replace", side_effect=spy_replace)

    status = install_mod.download_and_install_bundled(install_dir)

    assert status.status == "ready"
    assert call_order.index("verify_runs") < call_order.index("os.replace")


# ---- Stale-partial sweep on entry ------------------------------------

def test_stale_tmp_and_partial_cleaned_before_install(install_dir):
    """cleanup_stale_partials wipes leftover .tmp / .partial siblings."""
    install_root = install_dir.parent
    install_root.mkdir(parents=True, exist_ok=True)

    stale_partial = install_root / "8.1.partial"
    stale_partial.mkdir()
    (stale_partial / "junk").write_text("from prior run")

    stale_tmp = install_root / "8.1.tmp.tar.xz"
    stale_tmp.write_bytes(b"abandoned-archive")

    cleanup_mod.cleanup_stale_partials(install_root)

    assert not stale_partial.exists()
    assert not stale_tmp.exists()


def test_stale_partial_sweep_no_op_when_root_missing(tmp_path):
    """No-op when the install root does not yet exist (first-ever boot)."""
    nonexistent = tmp_path / "never-existed"
    cleanup_mod.cleanup_stale_partials(nonexistent)
    assert not nonexistent.exists()


def test_stale_zip_tmp_swept_too(install_dir):
    """The zip-suffix temp file form is also cleaned by the sweep."""
    install_root = install_dir.parent
    install_root.mkdir(parents=True, exist_ok=True)
    stale = install_root / "8.1.tmp.zip"
    stale.write_bytes(b"abandoned")
    cleanup_mod.cleanup_stale_partials(install_root)
    assert not stale.exists()


# ---- Other install-pipeline branches ---------------------------------

def test_unsupported_platform_returns_download_failed(install_dir, mocker):
    """Unknown platform_id -> download_failed (transport bucket)."""
    mocker.patch.object(install_mod, "_check_placeholders", return_value=True)
    mocker.patch.object(install_mod, "get_platform_id", return_value=None)
    replace_spy = mocker.patch.object(install_mod.os, "replace")

    status = install_mod.download_and_install_bundled(install_dir)

    assert status.status == "failed"
    assert status.error == "download_failed"
    replace_spy.assert_not_called()


# ---- Placeholder-SHA hard-fail (security gate) ----------------------

@pytest.mark.parametrize(
    "bad_value", ["", constants.PLACEHOLDER_SENTINEL],
    ids=["empty_string", "sentinel"],
)
def test_placeholder_sha_fails_closed(install_dir, bad_value):
    """Pipeline fails closed on empty/sentinel pinned SHA."""
    bad_shas = dict(constants.FFMPEG_SHA256)
    first_key = next(iter(bad_shas))
    bad_shas[first_key] = bad_value

    with patch.object(install_mod, "FFMPEG_SHA256", bad_shas):
        status = install_mod.download_and_install_bundled(install_dir)

    assert status.status == "failed"
    assert status.error == "placeholder_sha"
    assert status.error in CLOSED_VOCAB_ERRORS
