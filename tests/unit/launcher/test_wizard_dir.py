# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tests for ``launcher.wizard_dir`` (FR-L0a / FR-L6 / FR-L13)."""

from launcher import wizard_dir, wizard_ipc


class TestEnsureWizardDir:

    def test_creates_dir(self, tmp_path):
        result = wizard_dir.ensure_wizard_dir(tmp_path)
        assert result == tmp_path / "wizard"
        assert result.is_dir()

    def test_idempotent_when_exists(self, tmp_path):
        (tmp_path / "wizard").mkdir()
        result = wizard_dir.ensure_wizard_dir(tmp_path)
        assert result.is_dir()


class TestSweepStaleMarkers:

    def test_removes_all_known_marker_names(self, tmp_path):
        target_dir = tmp_path / "wizard"
        target_dir.mkdir()
        for name in (
            wizard_ipc.MARKER_WIZARD_DONE,
            wizard_ipc.MARKER_WIZARD_REJECT,
            wizard_ipc.MARKER_RUNTIME_FAILED,
            wizard_ipc.MARKER_LEGACY_SHUTDOWN,
        ):
            (target_dir / name).write_text("stale")
        wizard_dir.sweep_stale_markers(tmp_path)
        for name in (
            wizard_ipc.MARKER_WIZARD_DONE,
            wizard_ipc.MARKER_WIZARD_REJECT,
            wizard_ipc.MARKER_RUNTIME_FAILED,
            wizard_ipc.MARKER_LEGACY_SHUTDOWN,
        ):
            assert not (target_dir / name).exists()

    def test_silent_when_dir_missing(self, tmp_path):
        # Must not raise.
        wizard_dir.sweep_stale_markers(tmp_path)

    def test_preserves_non_marker_files(self, tmp_path):
        target_dir = tmp_path / "wizard"
        target_dir.mkdir()
        keep = target_dir / "tls.crt"
        keep.write_text("cert")
        wizard_dir.sweep_stale_markers(tmp_path)
        assert keep.exists()


class TestWriteSecretFile:

    def test_writes_value(self, tmp_path):
        target = tmp_path / ".setup_token"
        wizard_dir.write_secret_file(target, b"my-token-bytes")
        assert target.read_bytes() == b"my-token-bytes"

    def test_rejects_non_bytes(self, tmp_path):
        import pytest
        with pytest.raises(TypeError):
            wizard_dir.write_secret_file(tmp_path / "x", "not-bytes")  # type: ignore[arg-type]

    def test_atomic_write_creates_parent_dir(self, tmp_path):
        target = tmp_path / "deep" / "nested" / ".setup_token"
        wizard_dir.write_secret_file(target, b"x")
        assert target.exists()


class TestCleanupWizardDir:

    def test_removes_dir_recursively(self, tmp_path):
        target_dir = tmp_path / "wizard"
        target_dir.mkdir()
        (target_dir / "tls.crt").write_text("cert")
        (target_dir / "subdir").mkdir()
        (target_dir / "subdir" / "log").write_text("log")
        wizard_dir.cleanup_wizard_dir(tmp_path)
        assert not target_dir.exists()

    def test_silent_when_dir_missing(self, tmp_path):
        wizard_dir.cleanup_wizard_dir(tmp_path)
