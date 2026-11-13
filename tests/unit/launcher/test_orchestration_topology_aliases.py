# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Topology spellings at every launcher match site (issue #210).

The wizard writes ``worker_only`` / ``manager_worker``; the launcher
used to match only ``worker`` / ``manager_worker`` / ``manager+worker``,
so a worker-only install spawned nothing in normal mode. Every site now
goes through :mod:`shared.topology`.
"""

from __future__ import annotations

import argparse
import json
import logging
from unittest.mock import MagicMock

import pytest

from launcher import (
    apply_pending_setup, orchestration, runtime_topology, setup_sentinel,
    wizard_runtime,
)

_WORKER_ALIASES = ["worker", "worker_only"]
_BOTH_ALIASES = ["manager_worker", "manager+worker"]


def _args_ns():
    return argparse.Namespace(no_browser=True, print_url=True)


def _write_topology(data_dir, topo):
    (data_dir / "topology.json").write_text(
        json.dumps({"topology": topo}), encoding="utf-8",
    )


@pytest.fixture
def normal_mode(mocker):
    """Stub normal mode's side effects; record spawns + probed URLs."""
    mocker.patch.object(orchestration, "remove_setup_section")
    mocker.patch.object(
        orchestration, "_all_live_exited", return_value=True,
    )
    stubs = {
        "caddy": mocker.patch.object(orchestration, "start_caddy_supervisor"),
        "browser": mocker.patch.object(orchestration, "open_browser"),
        "wait": mocker.patch.object(
            orchestration, "wait_for_health", return_value=True,
        ),
        "spawned": [],
    }

    def _start(name, *_a, **_k):
        stubs["spawned"].append(name)
        return MagicMock(name=name)
    stubs["start"] = _start
    return stubs


def _run(data_dir, stubs):
    return orchestration.run_normal_mode(
        data_dir, _args_ns(), tray=None, secret="s",
        start_component=stubs["start"],
    )


def _probed(stubs):
    return [c.args[0] for c in stubs["wait"].call_args_list]


class TestRunNormalModeWorkerOnly:

    @pytest.mark.parametrize("topo", _WORKER_ALIASES)
    def test_spawns_worker_and_probes_8081(self, tmp_path, normal_mode, topo):
        _write_topology(tmp_path, topo)
        assert _run(tmp_path, normal_mode) == 0
        assert normal_mode["spawned"] == ["worker"]
        assert _probed(normal_mode) == [orchestration._WORKER_HEALTH_URL]
        assert ":8081/" in orchestration._WORKER_HEALTH_URL
        normal_mode["caddy"].assert_not_called()
        normal_mode["browser"].assert_not_called()


class TestRunNormalModeOtherTopologies:

    @pytest.mark.parametrize("topo", _BOTH_ALIASES)
    def test_manager_worker_aliases(self, tmp_path, normal_mode, topo):
        _write_topology(tmp_path, topo)
        assert _run(tmp_path, normal_mode) == 0
        assert normal_mode["spawned"] == ["manager", "worker"]
        assert _probed(normal_mode) == [
            orchestration._MANAGER_HEALTH_URL,
            orchestration._WORKER_HEALTH_URL,
        ]
        normal_mode["caddy"].assert_called_once()
        normal_mode["browser"].assert_called_once()

    def test_manager_only(self, tmp_path, normal_mode):
        _write_topology(tmp_path, "manager")
        assert _run(tmp_path, normal_mode) == 0
        assert normal_mode["spawned"] == ["manager"]
        assert _probed(normal_mode) == [orchestration._MANAGER_HEALTH_URL]

    def test_missing_topology_file_keeps_manager_worker_default(
        self, tmp_path, normal_mode,
    ):
        assert _run(tmp_path, normal_mode) == 0
        assert normal_mode["spawned"] == ["manager", "worker"]

    def test_unknown_topology_without_record_fails_closed(
        self, tmp_path, normal_mode, caplog,
    ):
        """Used to spawn nothing and supervise forever; must not widen
        to manager+worker either. Nothing spawns, rc=1, splash error."""
        _write_topology(tmp_path, "universe")
        failed = MagicMock()
        with caplog.at_level(logging.ERROR, logger="launcher.runtime_topology"):
            rc = orchestration.run_normal_mode(
                tmp_path, _args_ns(), tray=None, secret="s",
                start_component=normal_mode["start"],
                on_startup_failed=failed,
            )
        assert rc == 1
        assert normal_mode["spawned"] == []
        normal_mode["caddy"].assert_not_called()
        normal_mode["browser"].assert_not_called()
        normal_mode["wait"].assert_not_called()
        failed.assert_called_once_with(
            runtime_topology.REASON_UNKNOWN_TOPOLOGY, "",
        )
        assert any("refusing" in r.message for r in caplog.records)

    @pytest.mark.parametrize("raw", ["universe", 3, None, ["worker"]])
    def test_unknown_topology_falls_back_to_recorded_worker(
        self, tmp_path, normal_mode, raw,
    ):
        _write_topology(tmp_path, raw)
        setup_sentinel.record_setup_complete(tmp_path, "worker_only")
        assert _run(tmp_path, normal_mode) == 0
        assert normal_mode["spawned"] == ["worker"]
        normal_mode["caddy"].assert_not_called()
        normal_mode["browser"].assert_not_called()

    def test_missing_topology_file_prefers_recorded_worker(
        self, tmp_path, normal_mode,
    ):
        setup_sentinel.record_setup_complete(tmp_path, "worker_only")
        assert _run(tmp_path, normal_mode) == 0
        assert normal_mode["spawned"] == ["worker"]

    @pytest.mark.parametrize("body", ["[]", "{not json", '"worker"'])
    def test_malformed_or_non_object_json_keeps_default(
        self, tmp_path, normal_mode, body,
    ):
        (tmp_path / "topology.json").write_text(body, encoding="utf-8")
        assert _run(tmp_path, normal_mode) == 0
        assert normal_mode["spawned"] == ["manager", "worker"]


class TestAwaitColdBootAliases:

    @pytest.mark.parametrize("topo", _WORKER_ALIASES)
    def test_worker_aliases_probe_worker(self, mocker, topo):
        wait = mocker.patch.object(
            orchestration, "wait_for_health", return_value=True,
        )
        ready = MagicMock()
        rc = orchestration._await_cold_boot(
            topo, None, MagicMock(), ready, MagicMock(),
        )
        assert rc is None
        ready.assert_called_once()
        assert [c.args[0] for c in wait.call_args_list] == [
            orchestration._WORKER_HEALTH_URL,
        ]


class TestApplyPipelineGate:

    @pytest.mark.parametrize(
        "topo", ["manager", "manager_worker", "manager+worker"],
    )
    def test_manager_bearing_runs_pipeline(self, mocker, tmp_path, topo):
        pipeline = mocker.patch.object(
            apply_pending_setup, "run_apply_pipeline",
            return_value=(True, ""),
        )
        assert apply_pending_setup.run_apply_pipeline_if_needed(
            topo, tmp_path, None, MagicMock(), MagicMock(),
        ) is None
        pipeline.assert_called_once_with(tmp_path)

    @pytest.mark.parametrize("topo", _WORKER_ALIASES + ["", "universe"])
    def test_others_skip_pipeline(self, mocker, tmp_path, topo):
        pipeline = mocker.patch.object(
            apply_pending_setup, "run_apply_pipeline",
        )
        assert apply_pending_setup.run_apply_pipeline_if_needed(
            topo, tmp_path, None, MagicMock(), MagicMock(),
        ) is None
        pipeline.assert_not_called()


class TestSpawnRuntimeForTopology:

    @pytest.mark.parametrize("topo", _WORKER_ALIASES)
    def test_worker_aliases_spawn_worker(self, tmp_path, topo):
        start = MagicMock()
        proc, port = wizard_runtime.spawn_runtime_for_topology(
            topo, MagicMock(), start, tmp_path,
        )
        start.assert_called_once_with("worker")
        assert proc is start.return_value
        assert port is None

    @pytest.mark.parametrize("topo", ["manager"] + _BOTH_ALIASES)
    def test_manager_aliases_spawn_manager(self, tmp_path, topo):
        start = MagicMock()
        _proc, port = wizard_runtime.spawn_runtime_for_topology(
            topo, MagicMock(), start, tmp_path,
        )
        start.assert_called_once_with("manager")
        assert port == 8080
