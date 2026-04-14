# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``web_ui/setup/handlers_discovery.py``.

Covers handle_discover and handle_select_manager ASGI handlers.
Module state is reset by the autouse fixture in ``conftest.py``.
"""

import asyncio
import json

from sethlans_worker_agent.web_ui.setup.handlers_discovery import (
    handle_discover,
    handle_select_manager,
    get_selected_manager_url,
    get_selected_manager_id,
    _build_manager_url,
)

from tests.unit.worker._asgi_helpers import (
    make_scope,
    make_receive,
    ResponseCollector,
)


def _run(coro):
    return asyncio.run(coro)


SAMPLE_ANNOUNCEMENTS = {
    "00000000-0000-0000-0000-000000000042": {
        "name": "Lab Manager",
        "host": "lab.example",
        "ip": "10.0.0.1",
        "port": 8080,
        "manager_id": "00000000-0000-0000-0000-000000000042",
        "version": "0.1.0",
    },
}


# -------------------------------------------------------------------
# handle_discover
# -------------------------------------------------------------------

class TestHandleDiscover:
    def test_returns_managers_list(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_discovery"
            "._run_discovery",
            return_value=[
                {
                    "name": "Lab Manager",
                    "host": "lab.example",
                    "ip": "10.0.0.1",
                    "port": 8080,
                    "manager_id": "00000000-0000-0000-0000-000000000042",
                    "version": "0.1.0",
                },
            ],
        )

        scope = make_scope(path='/api/setup/discover/')
        collector = ResponseCollector()
        _run(handle_discover(scope, make_receive(), collector))

        assert collector.status == 200
        body = collector.json
        assert len(body["managers"]) == 1
        assert body["managers"][0]["name"] == "Lab Manager"

    def test_returns_empty_list_when_none_found(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_discovery"
            "._run_discovery",
            return_value=[],
        )

        scope = make_scope(path='/api/setup/discover/')
        collector = ResponseCollector()
        _run(handle_discover(scope, make_receive(), collector))

        assert collector.status == 200
        assert collector.json["managers"] == []


# -------------------------------------------------------------------
# handle_select_manager
# -------------------------------------------------------------------

class TestHandleSelectManager:
    def test_stores_manager_url_and_id(self):
        body = json.dumps({
            "ip": "10.0.0.1",
            "host": "lab.example",
            "port": 8080,
            "manager_id": "test-manager-id",
        }).encode()
        scope = make_scope(
            method='POST', path='/api/setup/worker/select-manager/',
        )
        collector = ResponseCollector()
        _run(handle_select_manager(
            scope, make_receive(body), collector,
        ))

        assert collector.status == 200
        resp = collector.json
        assert resp["status"] == "ok"
        assert "10.0.0.1" in resp["manager_url"]
        assert resp["manager_id"] == "test-manager-id"
        assert get_selected_manager_url() == resp["manager_url"]
        assert get_selected_manager_id() == "test-manager-id"

    def test_rejects_missing_url_and_host(self):
        body = json.dumps({
            "port": 8080,
            "manager_id": "test-id",
        }).encode()
        scope = make_scope(
            method='POST', path='/api/setup/worker/select-manager/',
        )
        collector = ResponseCollector()
        _run(handle_select_manager(
            scope, make_receive(body), collector,
        ))

        assert collector.status == 400
        assert "manager_url" in collector.json["error"] or \
            "ip" in collector.json["error"]

    def test_accepts_host_without_ip(self):
        body = json.dumps({
            "host": "lab.example",
            "port": 8080,
        }).encode()
        scope = make_scope(
            method='POST', path='/api/setup/worker/select-manager/',
        )
        collector = ResponseCollector()
        _run(handle_select_manager(
            scope, make_receive(body), collector,
        ))

        assert collector.status == 200
        assert "lab.example" in get_selected_manager_url()

    def test_accepts_ip_without_host(self):
        body = json.dumps({
            "ip": "192.168.1.100",
            "port": 9090,
        }).encode()
        scope = make_scope(
            method='POST', path='/api/setup/worker/select-manager/',
        )
        collector = ResponseCollector()
        _run(handle_select_manager(
            scope, make_receive(body), collector,
        ))

        assert collector.status == 200
        assert "192.168.1.100" in get_selected_manager_url()

    def test_accepts_manager_url_directly(self):
        body = json.dumps({
            "manager_url": "https://10.0.0.1:8080/api/",
            "manager_id": "mid-123",
        }).encode()
        scope = make_scope(
            method='POST', path='/api/setup/worker/select-manager/',
        )
        collector = ResponseCollector()
        _run(handle_select_manager(
            scope, make_receive(body), collector,
        ))

        assert collector.status == 200
        assert get_selected_manager_url() == "https://10.0.0.1:8080/api/"
        assert get_selected_manager_id() == "mid-123"

    def test_rejects_non_object_body(self):
        body = json.dumps("not-object").encode()
        scope = make_scope(
            method='POST', path='/api/setup/worker/select-manager/',
        )
        collector = ResponseCollector()
        _run(handle_select_manager(
            scope, make_receive(body), collector,
        ))

        assert collector.status == 400

    def test_appends_checkpoint(self):
        from sethlans_worker_agent.web_ui.setup.handlers_status import (
            get_current_checkpoints,
        )
        body = json.dumps({
            "ip": "10.0.0.1",
            "port": 8080,
        }).encode()
        scope = make_scope(
            method='POST', path='/api/setup/worker/select-manager/',
        )
        collector = ResponseCollector()
        _run(handle_select_manager(
            scope, make_receive(body), collector,
        ))

        assert "manager_selected" in get_current_checkpoints()


# -------------------------------------------------------------------
# _build_manager_url
# -------------------------------------------------------------------

class TestBuildManagerUrl:
    def test_uses_ip_first(self):
        url = _build_manager_url({"ip": "10.0.0.1", "host": "h", "port": 8080})
        assert url == "https://10.0.0.1:8080/api/"

    def test_falls_back_to_host(self):
        url = _build_manager_url({"host": "lab.example", "port": 9090})
        assert url == "https://lab.example:9090/api/"

    def test_default_port(self):
        url = _build_manager_url({"ip": "10.0.0.1"})
        assert url == "https://10.0.0.1:8080/api/"
