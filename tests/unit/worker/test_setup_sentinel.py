# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``web_ui/setup/sentinel.py``.

Covers sentinel read/write, checkpoint append, setup-complete check,
and create_sentinel.  All filesystem tests use ``tmp_path`` for
isolation.
"""

import json
import threading
from unittest.mock import patch

from sethlans_worker_agent.web_ui.setup.sentinel import (
    SENTINEL_FILENAME,
    SENTINEL_VERSION,
    read_sentinel,
    write_sentinel,
    append_checkpoint,
    is_setup_complete,
    create_sentinel,
)


# -------------------------------------------------------------------
# read_sentinel
# -------------------------------------------------------------------

class TestReadSentinel:
    def test_valid_file(self, tmp_path):
        data = {
            "version": SENTINEL_VERSION,
            "completed_at": "2025-01-15T12:00:00Z",
            "topology": "worker_only",
            "checkpoints": ["topology_chosen"],
        }
        (tmp_path / SENTINEL_FILENAME).write_text(
            json.dumps(data), encoding="utf-8",
        )
        result = read_sentinel(tmp_path)
        assert result is not None
        assert result["topology"] == "worker_only"
        assert result["checkpoints"] == ["topology_chosen"]

    def test_missing_file_returns_none(self, tmp_path):
        assert read_sentinel(tmp_path) is None

    def test_malformed_json_returns_none(self, tmp_path):
        (tmp_path / SENTINEL_FILENAME).write_text(
            "{not-valid-json", encoding="utf-8",
        )
        assert read_sentinel(tmp_path) is None

    def test_wrong_version_returns_none(self, tmp_path):
        data = {"version": 999, "checkpoints": []}
        (tmp_path / SENTINEL_FILENAME).write_text(
            json.dumps(data), encoding="utf-8",
        )
        assert read_sentinel(tmp_path) is None

    def test_non_dict_json_returns_none(self, tmp_path):
        (tmp_path / SENTINEL_FILENAME).write_text(
            "[1,2,3]", encoding="utf-8",
        )
        assert read_sentinel(tmp_path) is None

    def test_missing_version_returns_none(self, tmp_path):
        data = {"topology": "worker_only", "checkpoints": []}
        (tmp_path / SENTINEL_FILENAME).write_text(
            json.dumps(data), encoding="utf-8",
        )
        assert read_sentinel(tmp_path) is None


# -------------------------------------------------------------------
# write_sentinel
# -------------------------------------------------------------------

class TestWriteSentinel:
    def test_creates_file_with_correct_json(self, tmp_path):
        data = {
            "version": SENTINEL_VERSION,
            "completed_at": "2025-01-15T12:00:00Z",
            "topology": "worker_only",
            "checkpoints": ["topology_chosen"],
        }
        write_sentinel(tmp_path, data)

        sentinel_path = tmp_path / SENTINEL_FILENAME
        assert sentinel_path.exists()
        written = json.loads(sentinel_path.read_text(encoding="utf-8"))
        assert written == data

    def test_creates_parent_directory(self, tmp_path):
        nested = tmp_path / "sub" / "dir"
        data = {"version": SENTINEL_VERSION, "checkpoints": []}
        write_sentinel(nested, data)
        assert (nested / SENTINEL_FILENAME).exists()

    def test_atomic_write_uses_temp_then_replace(self, tmp_path):
        """Verify os.replace is called (atomic rename)."""
        data = {"version": SENTINEL_VERSION, "checkpoints": []}
        with patch(
            "sethlans_worker_agent.web_ui.setup.sentinel.os.replace",
            wraps=__import__("os").replace,
        ) as mock_replace:
            write_sentinel(tmp_path, data)
            assert mock_replace.call_count == 1

    def test_overwrites_existing_sentinel(self, tmp_path):
        data1 = {"version": SENTINEL_VERSION, "checkpoints": ["a"]}
        data2 = {"version": SENTINEL_VERSION, "checkpoints": ["a", "b"]}
        write_sentinel(tmp_path, data1)
        write_sentinel(tmp_path, data2)
        written = json.loads(
            (tmp_path / SENTINEL_FILENAME).read_text(encoding="utf-8"),
        )
        assert written["checkpoints"] == ["a", "b"]


# -------------------------------------------------------------------
# append_checkpoint
# -------------------------------------------------------------------

class TestAppendCheckpoint:
    def test_adds_checkpoint_to_empty_file(self, tmp_path):
        append_checkpoint(tmp_path, "topology_chosen")
        data = read_sentinel(tmp_path)
        assert data is not None
        assert "topology_chosen" in data["checkpoints"]

    def test_skips_duplicate_checkpoint(self, tmp_path):
        append_checkpoint(tmp_path, "enrolled")
        append_checkpoint(tmp_path, "enrolled")
        data = read_sentinel(tmp_path)
        assert data["checkpoints"].count("enrolled") == 1

    def test_preserves_existing_checkpoints(self, tmp_path):
        append_checkpoint(tmp_path, "topology_chosen")
        append_checkpoint(tmp_path, "enrolled")
        data = read_sentinel(tmp_path)
        assert data["checkpoints"] == ["topology_chosen", "enrolled"]

    def test_thread_safety(self, tmp_path):
        """Multiple threads appending concurrently do not lose data."""
        errors = []

        def worker(name):
            try:
                append_checkpoint(tmp_path, name)
            except Exception as e:
                errors.append(e)

        names = [f"step_{i}" for i in range(5)]
        threads = [
            threading.Thread(target=worker, args=(n,))
            for n in names
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []
        data = read_sentinel(tmp_path)
        assert sorted(data["checkpoints"]) == sorted(names)


# -------------------------------------------------------------------
# is_setup_complete
# -------------------------------------------------------------------

class TestIsSetupComplete:
    def test_returns_true_when_sentinel_valid(self, tmp_path):
        data = {"version": SENTINEL_VERSION, "checkpoints": []}
        write_sentinel(tmp_path, data)
        assert is_setup_complete(tmp_path) is True

    def test_returns_false_when_missing(self, tmp_path):
        assert is_setup_complete(tmp_path) is False


# -------------------------------------------------------------------
# create_sentinel
# -------------------------------------------------------------------

class TestCreateSentinel:
    def test_creates_with_topology_and_checkpoints(self, tmp_path):
        create_sentinel(
            tmp_path, "worker_only",
            ["topology_chosen", "enrolled"],
        )
        data = read_sentinel(tmp_path)
        assert data is not None
        assert data["version"] == SENTINEL_VERSION
        assert data["topology"] == "worker_only"
        assert data["checkpoints"] == [
            "topology_chosen", "enrolled",
        ]
        assert data["completed_at"] is not None
