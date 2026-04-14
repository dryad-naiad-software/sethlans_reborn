# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``web_ui/setup/handlers_status.py``.

Covers handle_setup_status and handle_set_topology ASGI handlers.
Module state is reset by the autouse fixture in ``conftest.py``.
"""

import asyncio
import json

from sethlans_worker_agent.web_ui.setup import handlers_status
from sethlans_worker_agent.web_ui.setup.handlers_status import (
    handle_setup_status,
    handle_set_topology,
    get_current_topology,
    get_current_checkpoints,
    append_wizard_checkpoint,
)

from tests.unit.worker._asgi_helpers import (
    make_scope,
    make_receive,
    ResponseCollector,
)


def _run(coro):
    return asyncio.run(coro)


# -------------------------------------------------------------------
# handle_setup_status
# -------------------------------------------------------------------

class TestHandleSetupStatus:
    def test_returns_complete_when_sentinel_exists(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_status"
            ".config_store.get_data_dir",
            return_value="/fake/dir",
        )
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_status"
            ".read_sentinel",
            return_value={
                "version": 1,
                "topology": "worker_only",
                "checkpoints": ["topology_chosen", "enrolled"],
            },
        )

        scope = make_scope(path='/api/setup/status/')
        collector = ResponseCollector()
        _run(handle_setup_status(scope, make_receive(), collector))

        assert collector.status == 200
        body = collector.json
        assert body["complete"] is True
        assert body["topology"] == "worker_only"
        assert "topology_chosen" in body["checkpoints"]

    def test_returns_incomplete_with_in_progress_state(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_status"
            ".config_store.get_data_dir",
            return_value="/fake/dir",
        )
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_status"
            ".read_sentinel",
            return_value=None,
        )
        # Set some in-progress state.
        handlers_status._current_topology = "worker_only"
        handlers_status._current_checkpoints = ["topology_chosen"]

        scope = make_scope(path='/api/setup/status/')
        collector = ResponseCollector()
        _run(handle_setup_status(scope, make_receive(), collector))

        assert collector.status == 200
        body = collector.json
        assert body["complete"] is False
        assert body["topology"] == "worker_only"
        assert body["checkpoints"] == ["topology_chosen"]


# -------------------------------------------------------------------
# handle_set_topology
# -------------------------------------------------------------------

class TestHandleSetTopology:
    def test_accepts_worker_only(self):
        body = json.dumps({"topology": "worker_only"}).encode()
        scope = make_scope(method='POST', path='/api/setup/topology/')
        collector = ResponseCollector()
        _run(handle_set_topology(scope, make_receive(body), collector))

        assert collector.status == 200
        resp = collector.json
        assert resp["status"] == "ok"
        assert resp["topology"] == "worker_only"
        assert "topology_chosen" in resp["checkpoints"]
        assert get_current_topology() == "worker_only"

    def test_rejects_invalid_topology(self):
        body = json.dumps({"topology": "manager"}).encode()
        scope = make_scope(method='POST', path='/api/setup/topology/')
        collector = ResponseCollector()
        _run(handle_set_topology(scope, make_receive(body), collector))

        assert collector.status == 400
        assert "Invalid topology" in collector.json["error"]

    def test_rejects_missing_topology(self):
        body = json.dumps({}).encode()
        scope = make_scope(method='POST', path='/api/setup/topology/')
        collector = ResponseCollector()
        _run(handle_set_topology(scope, make_receive(body), collector))

        assert collector.status == 400

    def test_rejects_non_object_body(self):
        body = json.dumps([1, 2]).encode()
        scope = make_scope(method='POST', path='/api/setup/topology/')
        collector = ResponseCollector()
        _run(handle_set_topology(scope, make_receive(body), collector))

        assert collector.status == 400
        assert "JSON object" in collector.json["error"]

    def test_tracks_checkpoint(self):
        body = json.dumps({"topology": "worker_only"}).encode()
        scope = make_scope(method='POST', path='/api/setup/topology/')
        collector = ResponseCollector()
        _run(handle_set_topology(scope, make_receive(body), collector))

        assert "topology_chosen" in get_current_checkpoints()


# -------------------------------------------------------------------
# append_wizard_checkpoint
# -------------------------------------------------------------------

class TestAppendWizardCheckpoint:
    def test_appends_checkpoint(self):
        append_wizard_checkpoint("enrolled")
        assert "enrolled" in get_current_checkpoints()

    def test_skips_duplicate(self):
        append_wizard_checkpoint("enrolled")
        append_wizard_checkpoint("enrolled")
        assert get_current_checkpoints().count("enrolled") == 1
