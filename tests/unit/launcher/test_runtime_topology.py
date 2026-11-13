# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``launcher.runtime_topology`` (issue #210).

Normal mode must fail closed: a present-but-unrecognized ``topology.json``
value falls back to the topology recorded in ``.setup_complete`` and,
failing that, refuses to start anything.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock

import pytest

from launcher import runtime_topology as rt
from launcher import setup_sentinel


def _record(data_dir, topology):
    setup_sentinel._load_sentinel_module().create_sentinel(
        data_dir=data_dir, topology=topology, checkpoints=[],
    )


class TestReadTopologyJson:

    def test_missing_file(self, tmp_path):
        assert rt.read_topology_json(tmp_path) == {}

    def test_object(self, tmp_path):
        (tmp_path / "topology.json").write_text(
            json.dumps({"topology": "worker_only"}), encoding="utf-8",
        )
        assert rt.read_topology_json(tmp_path) == {"topology": "worker_only"}

    @pytest.mark.parametrize(
        "body", ["[]", '"worker"', "3", "null", "{not json"],
    )
    def test_non_object_or_malformed_is_empty(self, tmp_path, body):
        (tmp_path / "topology.json").write_text(body, encoding="utf-8")
        assert rt.read_topology_json(tmp_path) == {}

    def test_undecodable_bytes_is_empty(self, tmp_path):
        (tmp_path / "topology.json").write_bytes(b"\xff\xfe\x00")
        assert rt.read_topology_json(tmp_path) == {}


class TestRecognizedValue:

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("worker_only", "worker"), ("manager_worker", "manager+worker"),
         ("manager", "manager")],
    )
    def test_topology_json_wins(self, tmp_path, raw, expected):
        _record(tmp_path, "manager")  # ignored when topology.json is valid
        assert rt.resolve_runtime_topology(
            tmp_path, {"topology": raw},
        ) == expected


class TestMissingValue:

    @pytest.mark.parametrize("payload", [{}, {"other": "x"}, None, []])
    def test_default_when_nothing_recorded(self, tmp_path, payload):
        assert rt.resolve_runtime_topology(tmp_path, payload) == "manager+worker"

    @pytest.mark.parametrize("payload", [{}, {"other": "x"}])
    def test_prefers_recorded_topology(self, tmp_path, payload):
        _record(tmp_path, "worker_only")
        assert rt.resolve_runtime_topology(tmp_path, payload) == "worker"


class TestPresentButUnrecognized:

    @pytest.mark.parametrize(
        "raw", ["universe", "WORKER_ONLY", "", 3, None, ["worker"], {}],
    )
    def test_falls_back_to_recorded(self, tmp_path, raw, caplog):
        _record(tmp_path, "worker_only")
        with caplog.at_level(logging.WARNING, logger=rt.logger.name):
            assert rt.resolve_runtime_topology(
                tmp_path, {"topology": raw},
            ) == "worker"
        assert any("unknown topology" in r.message for r in caplog.records)

    def test_refuses_when_nothing_recorded(self, tmp_path, caplog):
        with caplog.at_level(logging.ERROR, logger=rt.logger.name):
            assert rt.resolve_runtime_topology(
                tmp_path, {"topology": "universe"},
            ) is None
        assert any("refusing" in r.message for r in caplog.records)

    def test_refuses_when_recorded_is_also_unknown(self, tmp_path):
        _record(tmp_path, "universe")
        assert rt.resolve_runtime_topology(
            tmp_path, {"topology": "galaxy"},
        ) is None

    def test_never_widens_to_manager(self, tmp_path):
        """The regression the review flagged: a hand-edited value on a
        worker-only box must not start the manager."""
        _record(tmp_path, "worker_only")
        topo = rt.resolve_runtime_topology(tmp_path, {"topology": "wrker"})
        assert topo == "worker"


class TestFailUnknownTopology:

    def test_reports_and_returns_one(self):
        failed = MagicMock()
        assert rt.fail_unknown_topology(failed) == 1
        failed.assert_called_once_with(rt.REASON_UNKNOWN_TOPOLOGY, "")

    def test_tolerates_missing_callback(self):
        assert rt.fail_unknown_topology(None) == 1
