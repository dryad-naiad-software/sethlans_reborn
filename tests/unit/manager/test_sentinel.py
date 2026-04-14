# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``manager/workers/services/sentinel.py``.

Covers sentinel read/write, checkpoint append (including thread
safety), setup-complete detection, and full sentinel creation.
"""

import json
import threading

from workers.services.sentinel import (
    SENTINEL_FILENAME,
    SENTINEL_VERSION,
    append_checkpoint,
    create_sentinel,
    is_setup_complete,
    read_sentinel,
    write_sentinel,
)


# ---- read_sentinel() -----------------------------------------------------

class TestReadSentinel:

    def test_valid_json(self, tmp_path):
        data = {
            "version": SENTINEL_VERSION,
            "completed_at": "2025-01-15T12:00:00Z",
            "topology": "manager",
            "checkpoints": ["topology_chosen"],
        }
        sentinel = tmp_path / SENTINEL_FILENAME
        sentinel.write_text(json.dumps(data), encoding="utf-8")
        result = read_sentinel(tmp_path)
        assert result == data

    def test_missing_file_returns_none(self, tmp_path):
        assert read_sentinel(tmp_path) is None

    def test_malformed_json_returns_none(self, tmp_path):
        sentinel = tmp_path / SENTINEL_FILENAME
        sentinel.write_text("{not json!!!", encoding="utf-8")
        assert read_sentinel(tmp_path) is None

    def test_wrong_version_returns_none(self, tmp_path):
        data = {"version": 999, "topology": "manager"}
        sentinel = tmp_path / SENTINEL_FILENAME
        sentinel.write_text(json.dumps(data), encoding="utf-8")
        assert read_sentinel(tmp_path) is None

    def test_non_dict_returns_none(self, tmp_path):
        sentinel = tmp_path / SENTINEL_FILENAME
        sentinel.write_text('["not", "a", "dict"]', encoding="utf-8")
        assert read_sentinel(tmp_path) is None

    def test_missing_version_returns_none(self, tmp_path):
        data = {"topology": "manager", "checkpoints": []}
        sentinel = tmp_path / SENTINEL_FILENAME
        sentinel.write_text(json.dumps(data), encoding="utf-8")
        assert read_sentinel(tmp_path) is None


# ---- write_sentinel() ----------------------------------------------------

class TestWriteSentinel:

    def test_creates_file(self, tmp_path):
        data = {
            "version": SENTINEL_VERSION,
            "topology": "manager",
            "checkpoints": [],
        }
        write_sentinel(tmp_path, data)
        sentinel = tmp_path / SENTINEL_FILENAME
        assert sentinel.exists()

    def test_file_is_valid_json(self, tmp_path):
        data = {
            "version": SENTINEL_VERSION,
            "topology": "manager_worker",
            "checkpoints": ["step1"],
        }
        write_sentinel(tmp_path, data)
        sentinel = tmp_path / SENTINEL_FILENAME
        parsed = json.loads(sentinel.read_text(encoding="utf-8"))
        assert parsed == data

    def test_creates_parent_dirs(self, tmp_path):
        nested = tmp_path / "sub" / "dir"
        data = {"version": SENTINEL_VERSION, "checkpoints": []}
        write_sentinel(nested, data)
        assert (nested / SENTINEL_FILENAME).exists()

    def test_overwrites_existing(self, tmp_path):
        old = {"version": SENTINEL_VERSION, "topology": "old"}
        new = {"version": SENTINEL_VERSION, "topology": "new"}
        write_sentinel(tmp_path, old)
        write_sentinel(tmp_path, new)
        result = read_sentinel(tmp_path)
        assert result["topology"] == "new"


# ---- append_checkpoint() -------------------------------------------------

class TestAppendCheckpoint:

    def test_adds_to_list(self, tmp_path):
        write_sentinel(tmp_path, {
            "version": SENTINEL_VERSION,
            "completed_at": None,
            "topology": None,
            "checkpoints": [],
        })
        append_checkpoint(tmp_path, "topology_chosen")
        result = read_sentinel(tmp_path)
        assert "topology_chosen" in result["checkpoints"]

    def test_no_duplicate_checkpoints(self, tmp_path):
        write_sentinel(tmp_path, {
            "version": SENTINEL_VERSION,
            "completed_at": None,
            "topology": None,
            "checkpoints": ["topology_chosen"],
        })
        append_checkpoint(tmp_path, "topology_chosen")
        result = read_sentinel(tmp_path)
        assert result["checkpoints"].count("topology_chosen") == 1

    def test_creates_sentinel_if_missing(self, tmp_path):
        append_checkpoint(tmp_path, "first_step")
        result = read_sentinel(tmp_path)
        assert result is not None
        assert "first_step" in result["checkpoints"]

    def test_thread_safety(self, tmp_path):
        """Spawn concurrent threads that append; verify none lost."""
        write_sentinel(tmp_path, {
            "version": SENTINEL_VERSION,
            "completed_at": None,
            "topology": None,
            "checkpoints": [],
        })
        names = [f"step_{i}" for i in range(20)]
        threads = [
            threading.Thread(
                target=append_checkpoint,
                args=(tmp_path, name),
            )
            for name in names
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        result = read_sentinel(tmp_path)
        for name in names:
            assert name in result["checkpoints"]
        assert len(result["checkpoints"]) == 20


# ---- is_setup_complete() -------------------------------------------------

class TestIsSetupComplete:

    def test_true_when_sentinel_exists(self, tmp_path):
        write_sentinel(tmp_path, {
            "version": SENTINEL_VERSION,
            "topology": "manager",
            "checkpoints": [],
        })
        assert is_setup_complete(tmp_path) is True

    def test_false_when_missing(self, tmp_path):
        assert is_setup_complete(tmp_path) is False

    def test_false_when_malformed(self, tmp_path):
        sentinel = tmp_path / SENTINEL_FILENAME
        sentinel.write_text("garbage", encoding="utf-8")
        assert is_setup_complete(tmp_path) is False


# ---- create_sentinel() ---------------------------------------------------

class TestCreateSentinel:

    def test_includes_required_fields(self, tmp_path):
        create_sentinel(
            tmp_path, "manager", ["step1", "step2"],
        )
        result = read_sentinel(tmp_path)
        assert result["version"] == SENTINEL_VERSION
        assert result["topology"] == "manager"
        assert result["checkpoints"] == ["step1", "step2"]
        assert "completed_at" in result

    def test_completed_at_is_iso_timestamp(self, tmp_path):
        create_sentinel(tmp_path, "worker_only", [])
        result = read_sentinel(tmp_path)
        # Should be parseable as ISO format
        from datetime import datetime
        datetime.fromisoformat(result["completed_at"])
