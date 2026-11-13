# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Unit tests for ``launcher.setup_sentinel`` (issue #210).

``record_setup_complete`` writes ``.setup_complete`` for worker-only
installs (the apply pipeline is skipped for them) using the manager's
own sentinel writer, and never touches the sentinel for manager
topologies, where ``apply_pending_setup`` is the writer.
"""

from __future__ import annotations

import json
import os

import pytest

from launcher import main_dispatch, setup_sentinel

SENTINEL = ".setup_complete"


def _sentinel_module():
    return setup_sentinel._load_sentinel_module()


class TestWritesForWorkerOnly:

    @pytest.mark.parametrize("topology", ["worker_only", "worker"])
    def test_writes_manager_format_sentinel(self, tmp_path, topology):
        assert setup_sentinel.record_setup_complete(tmp_path, topology) is True

        data = json.loads((tmp_path / SENTINEL).read_text(encoding="utf-8"))
        # Same version-1 envelope apply_pending_setup writes.
        assert data["version"] == _sentinel_module().SENTINEL_VERSION
        assert data["topology"] == topology
        assert data["checkpoints"] == []
        assert data["completed_at"]

    def test_every_reader_sees_setup_complete(self, tmp_path):
        setup_sentinel.record_setup_complete(tmp_path, "worker_only")
        # Launcher guard (existence) + manager reader (completed_at).
        assert main_dispatch._is_setup_complete(tmp_path) is True
        assert _sentinel_module().is_setup_complete(tmp_path) is True

    def test_no_temp_files_left_behind(self, tmp_path):
        setup_sentinel.record_setup_complete(tmp_path, "worker_only")
        assert sorted(p.name for p in tmp_path.iterdir()) == [SENTINEL]

    def test_uses_the_manager_writer(self, tmp_path, mocker):
        """Single on-disk format: the launcher delegates to the manager's
        ``create_sentinel`` instead of serializing JSON itself."""
        module = _sentinel_module()
        create = mocker.patch.object(module, "create_sentinel")
        setup_sentinel.record_setup_complete(tmp_path, "worker_only")
        create.assert_called_once_with(
            data_dir=tmp_path, topology="worker_only", checkpoints=[],
        )


class TestDoesNotWrite:

    @pytest.mark.parametrize(
        "topology", ["manager", "manager_worker", "manager+worker"],
    )
    def test_manager_topologies_are_left_to_apply(self, tmp_path, topology):
        assert setup_sentinel.record_setup_complete(tmp_path, topology) is False
        assert not (tmp_path / SENTINEL).exists()

    @pytest.mark.parametrize(
        "topology", ["manager_worker", "manager"],
    )
    def test_manager_sentinel_from_apply_is_not_overwritten(
        self, tmp_path, topology,
    ):
        _sentinel_module().create_sentinel(
            data_dir=tmp_path, topology=topology, checkpoints=["applied"],
        )
        before = (tmp_path / SENTINEL).read_bytes()
        assert setup_sentinel.record_setup_complete(tmp_path, topology) is False
        assert (tmp_path / SENTINEL).read_bytes() == before

    @pytest.mark.parametrize("topology", ["", "universe", "WORKER"])
    def test_unknown_topology_fails_closed(self, tmp_path, topology):
        assert setup_sentinel.record_setup_complete(tmp_path, topology) is False
        assert not (tmp_path / SENTINEL).exists()

    def test_existing_complete_sentinel_is_preserved(self, tmp_path):
        _sentinel_module().create_sentinel(
            data_dir=tmp_path, topology="worker_only", checkpoints=["kept"],
        )
        before = (tmp_path / SENTINEL).read_bytes()
        assert setup_sentinel.record_setup_complete(
            tmp_path, "worker_only",
        ) is False
        assert (tmp_path / SENTINEL).read_bytes() == before

    def test_incomplete_checkpoint_record_is_completed(self, tmp_path):
        """A mid-wizard record (``completed_at: null``) is not "complete";
        it is replaced by a completed sentinel."""
        _sentinel_module().append_checkpoint(tmp_path, "topology_chosen")
        assert setup_sentinel.record_setup_complete(
            tmp_path, "worker_only",
        ) is True
        assert _sentinel_module().is_setup_complete(tmp_path) is True


class TestWriteFailure:

    def test_oserror_propagates_and_leaves_no_files(self, tmp_path, mocker):
        mocker.patch.object(os, "replace", side_effect=OSError("disk full"))
        with pytest.raises(OSError):
            setup_sentinel.record_setup_complete(tmp_path, "worker_only")
        assert list(tmp_path.iterdir()) == []


class TestRecordedTopology:

    def test_none_when_sentinel_missing(self, tmp_path):
        assert setup_sentinel.recorded_topology(tmp_path) is None

    @pytest.mark.parametrize(
        ("recorded", "expected"),
        [("worker_only", "worker"), ("manager_worker", "manager+worker"),
         ("manager", "manager")],
    )
    def test_returns_canonical_recorded_topology(
        self, tmp_path, recorded, expected,
    ):
        _sentinel_module().create_sentinel(
            data_dir=tmp_path, topology=recorded, checkpoints=[],
        )
        assert setup_sentinel.recorded_topology(tmp_path) == expected

    @pytest.mark.parametrize(
        "body", ["{}", "not json", '{"version": 1, "topology": "universe"}',
                 '{"version": 1, "topology": null}'],
    )
    def test_none_for_unusable_sentinel(self, tmp_path, body):
        (tmp_path / SENTINEL).write_text(body, encoding="utf-8")
        assert setup_sentinel.recorded_topology(tmp_path) is None

    def test_none_when_reader_unavailable(self, tmp_path, mocker):
        mocker.patch.object(
            setup_sentinel, "_load_sentinel_module",
            side_effect=ImportError("frozen build lacks module"),
        )
        assert setup_sentinel.recorded_topology(tmp_path) is None
