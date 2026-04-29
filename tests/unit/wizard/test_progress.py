# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Smoke tests for ``wizard/sethlans_wizard/progress.py`` (FR-CHK1)."""

from __future__ import annotations

import json

from wizard.sethlans_wizard import progress


class TestAppendCheckpoint:

    def test_creates_progress_file_on_first_append(self, tmp_path):
        added = progress.append_checkpoint(tmp_path, "welcome_seen", topology="manager")
        assert added is True
        path = tmp_path / progress.PROGRESS_FILENAME
        assert path.exists()
        payload = json.loads(path.read_text("utf-8"))
        assert payload["schema_version"] == 1
        assert payload["topology"] == "manager"
        assert payload["checkpoints"] == ["welcome_seen"]

    def test_idempotent_on_duplicate(self, tmp_path):
        progress.append_checkpoint(tmp_path, "welcome_seen", topology="manager")
        added_again = progress.append_checkpoint(tmp_path, "welcome_seen")
        assert added_again is False
        path = tmp_path / progress.PROGRESS_FILENAME
        payload = json.loads(path.read_text("utf-8"))
        assert payload["checkpoints"] == ["welcome_seen"]

    def test_appends_in_order(self, tmp_path):
        for name in ("welcome_seen", "topology_chosen", "network_configured"):
            progress.append_checkpoint(tmp_path, name, topology="manager")
        path = tmp_path / progress.PROGRESS_FILENAME
        payload = json.loads(path.read_text("utf-8"))
        assert payload["checkpoints"] == [
            "welcome_seen", "topology_chosen", "network_configured",
        ]


class TestReadCheckpoints:

    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert progress.read_checkpoints(tmp_path) == {}

    def test_round_trip(self, tmp_path):
        progress.append_checkpoint(tmp_path, "welcome_seen", topology="manager")
        payload = progress.read_checkpoints(tmp_path)
        assert payload["topology"] == "manager"
        assert "welcome_seen" in payload["checkpoints"]
