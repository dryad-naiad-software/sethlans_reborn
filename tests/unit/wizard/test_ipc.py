# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``wizard/sethlans_wizard/ipc.py`` (Spec 1 / A2).

Covers FR-IPC1 (.wizard_done write/read), FR-IPC4 (.wizard_reject
freshness + data_dir bind), FR-IPC8 (.runtime_failed),
SEC-MED-9 (data_dir signed field), SEC-MED-10 (validation enumeration),
SEC-MED-11 (secret-file unlink-after-read), and atomic write.
"""

from __future__ import annotations

import json
import os
import platform
import time

import pytest

from wizard.sethlans_wizard import ipc as ipc_mod
from wizard.sethlans_wizard.ipc import (
    MARKER_RUNTIME_FAILED,
    MARKER_WIZARD_DONE,
    MARKER_WIZARD_REJECT,
    read_marker,
    read_secret_file,
    write_marker,
)

_SECRET = b"this-is-a-fake-ipc-secret-bytes-32+chars-padded-out"


# ---------------------------------------------------------------------
# write_marker — happy path & guards
# ---------------------------------------------------------------------

class TestWriteMarker:

    def test_writes_wizard_done_with_required_fields(self, tmp_path):
        path = tmp_path / "wizard" / MARKER_WIZARD_DONE
        write_marker(
            path, "wizard_done", tmp_path, _SECRET,
            payload={
                "topology": "manager",
                "wizard_port": 8100,
                "wizard_pid": 4242,
            },
        )
        assert path.is_file()
        body = json.loads(path.read_text(encoding="utf-8"))
        assert body["type"] == "wizard_done"
        assert body["schema_version"] == 1
        assert body["topology"] == "manager"
        assert body["wizard_port"] == 8100
        assert body["wizard_pid"] == 4242
        # data_dir is canonicalised; must be string-equal to resolve().
        assert body["data_dir"] == str(tmp_path.resolve())
        assert isinstance(body["created_at_unix"], float)
        assert isinstance(body["hmac_sha256"], str)
        assert len(body["hmac_sha256"]) == 64  # hex SHA-256

    def test_writes_runtime_failed_with_reason(self, tmp_path):
        path = tmp_path / MARKER_RUNTIME_FAILED
        write_marker(
            path, "runtime_failed", tmp_path, _SECRET,
            payload={"reason": "port-bind not observed within 30s"},
        )
        body = json.loads(path.read_text(encoding="utf-8"))
        assert body["type"] == "runtime_failed"
        assert body["reason"] == "port-bind not observed within 30s"

    def test_unknown_marker_type_raises(self, tmp_path):
        path = tmp_path / ".bogus"
        with pytest.raises(ValueError, match="Unknown marker_type"):
            write_marker(path, "not_a_real_marker", tmp_path, _SECRET)

    def test_reserved_field_in_payload_raises(self, tmp_path):
        path = tmp_path / MARKER_WIZARD_DONE
        with pytest.raises(ValueError, match="reserved fields"):
            write_marker(
                path, "wizard_done", tmp_path, _SECRET,
                payload={"type": "wizard_done"},
            )

    def test_atomic_write_uses_temp_in_same_dir(self, tmp_path, monkeypatch):
        path = tmp_path / "wizard" / MARKER_WIZARD_DONE
        seen_srcs = []
        real_replace = os.replace

        def spy_replace(src, dst):
            seen_srcs.append(src)
            real_replace(src, dst)

        monkeypatch.setattr(ipc_mod.os, "replace", spy_replace)
        write_marker(path, "wizard_done", tmp_path, _SECRET, payload={"topology": "manager"})
        assert seen_srcs, "expected os.replace call"
        for src in seen_srcs:
            assert os.path.dirname(src) == str(path.parent)

    def test_posix_marker_mode_is_600(self, tmp_path):
        if platform.system() == "Windows":
            return
        path = tmp_path / MARKER_WIZARD_DONE
        write_marker(path, "wizard_done", tmp_path, _SECRET, payload={"topology": "manager"})
        assert (path.stat().st_mode & 0o777) == 0o600


# ---------------------------------------------------------------------
# read_marker — happy path & validation rules
# ---------------------------------------------------------------------

def _write(tmp_path, marker_type=MARKER_WIZARD_DONE, payload=None):
    path = tmp_path / marker_type
    write_marker(
        path,
        {
            MARKER_WIZARD_DONE: "wizard_done",
            MARKER_WIZARD_REJECT: "wizard_reject",
            MARKER_RUNTIME_FAILED: "runtime_failed",
        }[marker_type],
        tmp_path,
        _SECRET,
        payload=payload or {"topology": "manager"},
    )
    return path


class TestReadMarkerHappyPath:

    def test_round_trip_wizard_done(self, tmp_path):
        path = _write(tmp_path)
        result = read_marker(path, _SECRET, "wizard_done", tmp_path)
        assert result is not None
        assert result["type"] == "wizard_done"
        assert result["topology"] == "manager"

    def test_caller_must_delete_file(self, tmp_path):
        """read_marker must NOT delete the marker — that is the
        responsibility of the launcher (FR-IPC3) per the spec."""
        path = _write(tmp_path)
        read_marker(path, _SECRET, "wizard_done", tmp_path)
        assert path.exists()


class TestReadMarkerRejection:

    def test_missing_file_returns_none(self, tmp_path):
        result = read_marker(
            tmp_path / "absent", _SECRET, "wizard_done", tmp_path,
        )
        assert result is None

    def test_corrupt_json_returns_none(self, tmp_path):
        path = tmp_path / MARKER_WIZARD_DONE
        path.write_bytes(b"not json at all")
        assert read_marker(path, _SECRET, "wizard_done", tmp_path) is None

    def test_oversized_payload_returns_none(self, tmp_path):
        path = tmp_path / MARKER_WIZARD_DONE
        path.write_bytes(b"x" * 5000)
        assert read_marker(path, _SECRET, "wizard_done", tmp_path) is None

    def test_wrong_secret_returns_none(self, tmp_path):
        path = _write(tmp_path)
        result = read_marker(path, b"different-secret-bytes", "wizard_done", tmp_path)
        assert result is None

    def test_empty_secret_returns_none(self, tmp_path):
        path = _write(tmp_path)
        assert read_marker(path, b"", "wizard_done", tmp_path) is None

    def test_wrong_type_returns_none(self, tmp_path):
        path = _write(tmp_path, MARKER_WIZARD_DONE)
        # Reader expects wizard_reject but the marker is wizard_done.
        assert read_marker(path, _SECRET, "wizard_reject", tmp_path) is None

    def test_wrong_data_dir_returns_none(self, tmp_path):
        path = _write(tmp_path)
        other = tmp_path.parent / "other_dir"
        other.mkdir(exist_ok=True)
        assert read_marker(path, _SECRET, "wizard_done", other) is None

    def test_stale_marker_returns_none(self, tmp_path, monkeypatch):
        path = _write(tmp_path)
        # Pretend we are reading 120 seconds in the future.
        real_time = time.time
        future = real_time() + 120
        monkeypatch.setattr(ipc_mod.time, "time", lambda: future)
        assert read_marker(path, _SECRET, "wizard_done", tmp_path) is None

    def test_marker_in_far_future_returns_none(self, tmp_path):
        # Hand-craft a payload claiming to be from 2 minutes in the future.
        path = tmp_path / MARKER_WIZARD_DONE
        body = {
            "type": "wizard_done",
            "schema_version": 1,
            "data_dir": str(tmp_path.resolve()),
            "topology": "manager",
            "created_at_unix": time.time() + 120,
        }
        # Compute valid HMAC against this exact body.
        body["hmac_sha256"] = ipc_mod._compute_hmac(body, _SECRET)  # noqa: SLF001
        path.write_text(json.dumps(body), encoding="utf-8")
        assert read_marker(path, _SECRET, "wizard_done", tmp_path) is None

    def test_wrong_schema_version_returns_none(self, tmp_path):
        path = tmp_path / MARKER_WIZARD_DONE
        body = {
            "type": "wizard_done",
            "schema_version": 999,
            "data_dir": str(tmp_path.resolve()),
            "topology": "manager",
            "created_at_unix": time.time(),
        }
        body["hmac_sha256"] = ipc_mod._compute_hmac(body, _SECRET)  # noqa: SLF001
        path.write_text(json.dumps(body), encoding="utf-8")
        assert read_marker(path, _SECRET, "wizard_done", tmp_path) is None

    def test_missing_hmac_returns_none(self, tmp_path):
        path = tmp_path / MARKER_WIZARD_DONE
        body = {
            "type": "wizard_done",
            "schema_version": 1,
            "data_dir": str(tmp_path.resolve()),
            "topology": "manager",
            "created_at_unix": time.time(),
        }
        path.write_text(json.dumps(body), encoding="utf-8")
        assert read_marker(path, _SECRET, "wizard_done", tmp_path) is None


# ---------------------------------------------------------------------
# read_secret_file — SEC-MED-11
# ---------------------------------------------------------------------

class TestReadSecretFile:

    def test_returns_secret_bytes_and_unlinks(self, tmp_path):
        secret_file = tmp_path / ".ipc_secret"
        secret_file.write_bytes(b"the-secret-bytes\n")
        result = read_secret_file(secret_file)
        assert result == b"the-secret-bytes"
        assert not secret_file.exists()  # SEC-MED-11

    def test_strips_surrounding_whitespace(self, tmp_path):
        secret_file = tmp_path / ".ipc_secret"
        secret_file.write_bytes(b"  abcDEF\r\n")
        assert read_secret_file(secret_file) == b"abcDEF"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(OSError):
            read_secret_file(tmp_path / "no_such_file")
