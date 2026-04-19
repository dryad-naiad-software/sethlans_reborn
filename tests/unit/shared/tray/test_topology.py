# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``shared/tray/topology.py`` (tray spec FR-5)."""

from __future__ import annotations

import json
import logging

import pytest

from shared.tray import topology as topo_mod
from shared.tray.topology import (
    TOPOLOGY_BOTH,
    TOPOLOGY_MANAGER,
    TOPOLOGY_WORKER,
    read_topology,
)


class TestReadTopology:

    def test_valid_manager(self, tmp_path):
        (tmp_path / "topology.json").write_text(
            json.dumps({"topology": "manager"}), encoding="utf-8",
        )
        assert read_topology(tmp_path) == TOPOLOGY_MANAGER

    def test_valid_worker(self, tmp_path):
        (tmp_path / "topology.json").write_text(
            json.dumps({"topology": "worker"}), encoding="utf-8",
        )
        assert read_topology(tmp_path) == TOPOLOGY_WORKER

    def test_valid_manager_worker_combined(self, tmp_path):
        (tmp_path / "topology.json").write_text(
            json.dumps({"topology": "manager+worker"}), encoding="utf-8",
        )
        assert read_topology(tmp_path) == TOPOLOGY_BOTH

    def test_legacy_alias_manager_worker_underscore_normalized(self, tmp_path):
        (tmp_path / "topology.json").write_text(
            json.dumps({"topology": "manager_worker"}), encoding="utf-8",
        )
        # Legacy alias normalizes to canonical form.
        assert read_topology(tmp_path) == TOPOLOGY_BOTH

    def test_missing_file_defaults_silently(self, tmp_path):
        # No file present; default returned.
        assert read_topology(tmp_path) == TOPOLOGY_BOTH

    def test_oversized_file_defaults_with_warning(self, tmp_path, caplog):
        path = tmp_path / "topology.json"
        path.write_text(
            json.dumps({"topology": "manager"}) + (" " * 2000),
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING, logger=topo_mod.logger.name):
            result = read_topology(tmp_path)
        assert result == TOPOLOGY_BOTH
        assert any("bytes" in rec.message for rec in caplog.records)

    def test_invalid_json_defaults_with_warning(self, tmp_path, caplog):
        (tmp_path / "topology.json").write_text(
            "{not-json!!", encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING, logger=topo_mod.logger.name):
            result = read_topology(tmp_path)
        assert result == TOPOLOGY_BOTH
        assert any("malformed" in rec.message for rec in caplog.records)

    def test_unknown_value_defaults_with_warning(self, tmp_path, caplog):
        (tmp_path / "topology.json").write_text(
            json.dumps({"topology": "universe"}), encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING, logger=topo_mod.logger.name):
            result = read_topology(tmp_path)
        assert result == TOPOLOGY_BOTH
        assert any(
            "unknown topology" in rec.message
            for rec in caplog.records
        )

    def test_non_dict_payload_defaults(self, tmp_path):
        (tmp_path / "topology.json").write_text(
            json.dumps(["manager"]), encoding="utf-8",
        )
        assert read_topology(tmp_path) == TOPOLOGY_BOTH

    def test_missing_key_defaults(self, tmp_path):
        (tmp_path / "topology.json").write_text(
            json.dumps({"other": "manager"}), encoding="utf-8",
        )
        assert read_topology(tmp_path) == TOPOLOGY_BOTH

    def test_never_raises(self, tmp_path, mocker):
        # Simulate OSError in read_text.
        path = tmp_path / "topology.json"
        path.write_text(json.dumps({"topology": "manager"}),
                        encoding="utf-8")
        mocker.patch.object(
            topo_mod.Path, "read_text",
            side_effect=OSError("disk"),
        )
        try:
            result = read_topology(tmp_path)
        except Exception as exc:  # pragma: no cover
            pytest.fail(f"read_topology raised: {exc!r}")
        assert result == TOPOLOGY_BOTH
