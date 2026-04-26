# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Tests for ``launcher.wizard_ipc`` HMAC marker IPC.

Verifies the launcher's marker write/read/validate logic round-trips
with the wizard's ``wizard.sethlans_wizard.ipc`` module (same schema
and HMAC framing per FR-IPC1 / FR-IPC8).
"""

import json
import time

import pytest

from launcher import wizard_ipc

# Cross-bundle interop check: the launcher's marker writer must be
# readable by the wizard's reader and vice versa.
from wizard.sethlans_wizard import ipc as wizard_ipc_pkg


SECRET = b"a" * 32


# ---- write_marker --------------------------------------------------------

class TestWriteMarker:

    def test_writes_runtime_failed_marker(self, tmp_path):
        target = tmp_path / "wizard" / wizard_ipc.MARKER_RUNTIME_FAILED
        wizard_ipc.write_marker(
            target, "runtime_failed", tmp_path, SECRET,
            payload={"reason": "port_bind_timeout"},
        )
        assert target.exists()
        body = json.loads(target.read_bytes())
        assert body["type"] == "runtime_failed"
        assert body["schema_version"] == 1
        assert body["reason"] == "port_bind_timeout"
        assert "hmac_sha256" in body

    def test_rejects_unknown_marker_type(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown marker_type"):
            wizard_ipc.write_marker(
                tmp_path / "x", "bogus_type", tmp_path, SECRET,
            )

    def test_rejects_empty_secret(self, tmp_path):
        with pytest.raises(ValueError, match="non-empty bytes"):
            wizard_ipc.write_marker(
                tmp_path / "x", "runtime_failed", tmp_path, b"",
            )

    def test_rejects_reserved_field_in_payload(self, tmp_path):
        with pytest.raises(ValueError, match="reserved fields"):
            wizard_ipc.write_marker(
                tmp_path / "x", "runtime_failed", tmp_path, SECRET,
                payload={"type": "evil"},
            )


# ---- read_marker --------------------------------------------------------

class TestReadMarker:

    def test_round_trip(self, tmp_path):
        target = tmp_path / "wizard" / wizard_ipc.MARKER_WIZARD_DONE
        wizard_ipc.write_marker(
            target, "wizard_done", tmp_path, SECRET,
            payload={"topology": "manager", "wizard_port": 8100},
        )
        result = wizard_ipc.read_marker(
            target, SECRET, "wizard_done", tmp_path,
        )
        assert result is not None
        assert result["topology"] == "manager"
        assert result["wizard_port"] == 8100

    def test_returns_none_for_missing_file(self, tmp_path):
        result = wizard_ipc.read_marker(
            tmp_path / "absent", SECRET, "wizard_done", tmp_path,
        )
        assert result is None

    def test_returns_none_for_wrong_secret(self, tmp_path):
        target = tmp_path / "wizard" / wizard_ipc.MARKER_WIZARD_DONE
        wizard_ipc.write_marker(
            target, "wizard_done", tmp_path, SECRET,
        )
        result = wizard_ipc.read_marker(
            target, b"b" * 32, "wizard_done", tmp_path,
        )
        assert result is None

    def test_returns_none_for_wrong_type(self, tmp_path):
        target = tmp_path / "wizard" / wizard_ipc.MARKER_WIZARD_DONE
        wizard_ipc.write_marker(
            target, "wizard_done", tmp_path, SECRET,
        )
        result = wizard_ipc.read_marker(
            target, SECRET, "runtime_failed", tmp_path,
        )
        assert result is None

    def test_returns_none_for_wrong_data_dir(self, tmp_path):
        target = tmp_path / "wizard" / wizard_ipc.MARKER_WIZARD_DONE
        wizard_ipc.write_marker(
            target, "wizard_done", tmp_path, SECRET,
        )
        other = tmp_path / "other"
        other.mkdir()
        result = wizard_ipc.read_marker(
            target, SECRET, "wizard_done", other,
        )
        assert result is None

    def test_returns_none_for_stale_marker(self, tmp_path, mocker):
        target = tmp_path / "wizard" / wizard_ipc.MARKER_WIZARD_DONE
        wizard_ipc.write_marker(
            target, "wizard_done", tmp_path, SECRET,
        )
        # Patch time forward past the freshness window.
        mocker.patch(
            "launcher.wizard_ipc.time.time",
            return_value=time.time() + 3600,
        )
        result = wizard_ipc.read_marker(
            target, SECRET, "wizard_done", tmp_path,
        )
        assert result is None

    def test_returns_none_for_oversized_marker(self, tmp_path):
        target = tmp_path / "wizard" / wizard_ipc.MARKER_WIZARD_DONE
        target.parent.mkdir(parents=True)
        target.write_bytes(b"x" * 10_000)
        result = wizard_ipc.read_marker(
            target, SECRET, "wizard_done", tmp_path,
        )
        assert result is None


# ---- Cross-bundle interop ------------------------------------------------

class TestInteropWithWizardPackage:
    """Launcher write ↔ wizard read and wizard write ↔ launcher read."""

    def test_launcher_writes_wizard_reads(self, tmp_path):
        target = tmp_path / "wizard" / wizard_ipc.MARKER_RUNTIME_FAILED
        wizard_ipc.write_marker(
            target, "runtime_failed", tmp_path, SECRET,
            payload={"reason": "port_bind_timeout"},
        )
        result = wizard_ipc_pkg.read_marker(
            target, SECRET, "runtime_failed", tmp_path,
        )
        assert result is not None
        assert result["reason"] == "port_bind_timeout"

    def test_wizard_writes_launcher_reads(self, tmp_path):
        target = tmp_path / "wizard" / wizard_ipc.MARKER_WIZARD_DONE
        wizard_ipc_pkg.write_marker(
            target, "wizard_done", tmp_path, SECRET,
            payload={"topology": "manager_worker", "wizard_port": 8101},
        )
        result = wizard_ipc.read_marker(
            target, SECRET, "wizard_done", tmp_path,
        )
        assert result is not None
        assert result["topology"] == "manager_worker"
        assert result["wizard_port"] == 8101


# ---- delete_marker -------------------------------------------------------

class TestDeleteMarker:

    def test_deletes_existing_file(self, tmp_path):
        target = tmp_path / "marker"
        target.write_text("x")
        wizard_ipc.delete_marker(target)
        assert not target.exists()

    def test_silent_on_missing_file(self, tmp_path):
        # Must not raise.
        wizard_ipc.delete_marker(tmp_path / "absent")
