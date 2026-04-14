# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``web_ui/setup/handlers_enrollment.py``.

Covers handle_enroll ASGI handler with mocked enrollment_client,
config_store, and hardware_detection.  Module state is reset by
the autouse fixtures in ``conftest.py``.
"""

import asyncio
import json

from sethlans_worker_agent.web_ui.setup import handlers_discovery
from sethlans_worker_agent.web_ui.setup.handlers_enrollment import (
    handle_enroll,
)

from tests.unit.worker._asgi_helpers import (
    make_scope,
    make_receive,
    ResponseCollector,
)


def _run(coro):
    return asyncio.run(coro)


def _set_selected_manager():
    """Pre-populate the selected manager module state."""
    handlers_discovery._selected_manager_url = (
        "https://10.0.0.1:8080/api/"
    )
    handlers_discovery._selected_manager_id = "mgr-id-42"
    handlers_discovery._selected_manager_meta = {
        "ip": "10.0.0.1",
        "host": "lab.example",
        "port": 8080,
    }


def _make_scope():
    return make_scope(
        method='POST', path='/api/setup/worker/enroll/',
    )


class TestHandleEnroll:
    def test_success_returns_ok(self, mocker):
        _set_selected_manager()

        mock_enroll = mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_enrollment"
            "._do_enroll",
            return_value={
                "status": "ok",
                "manager_name": "Test Manager",
            },
        )

        body = json.dumps({
            "enrollment_key": "4F9XK2PBQ7M3N8RT",
        }).encode()
        collector = ResponseCollector()
        _run(handle_enroll(
            _make_scope(), make_receive(body), collector,
        ))

        assert collector.status == 200
        assert collector.json["status"] == "ok"
        assert collector.json["manager_name"] == "Test Manager"
        mock_enroll.assert_called_once()

    def test_returns_400_when_no_manager_selected(self):
        body = json.dumps({
            "enrollment_key": "4F9XK2PBQ7M3N8RT",
        }).encode()
        collector = ResponseCollector()
        _run(handle_enroll(
            _make_scope(), make_receive(body), collector,
        ))

        assert collector.status == 400
        assert "No manager selected" in collector.json["error"]

    def test_returns_400_when_key_empty(self):
        _set_selected_manager()

        body = json.dumps({
            "enrollment_key": "",
        }).encode()
        collector = ResponseCollector()
        _run(handle_enroll(
            _make_scope(), make_receive(body), collector,
        ))

        assert collector.status == 400
        assert "required" in collector.json["error"].lower()

    def test_returns_400_when_key_missing(self):
        _set_selected_manager()

        body = json.dumps({}).encode()
        collector = ResponseCollector()
        _run(handle_enroll(
            _make_scope(), make_receive(body), collector,
        ))

        assert collector.status == 400

    def test_returns_403_for_invalid_key(self, mocker):
        _set_selected_manager()
        from sethlans_worker_agent import enrollment_client

        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_enrollment"
            "._do_enroll",
            side_effect=enrollment_client.InvalidKeyError(
                "Invalid key"
            ),
        )

        body = json.dumps({
            "enrollment_key": "BADKEY1234567890",
        }).encode()
        collector = ResponseCollector()
        _run(handle_enroll(
            _make_scope(), make_receive(body), collector,
        ))

        assert collector.status == 403
        assert "Invalid key" in collector.json["error"]

    def test_returns_429_for_rate_limited(self, mocker):
        _set_selected_manager()
        from sethlans_worker_agent import enrollment_client

        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_enrollment"
            "._do_enroll",
            side_effect=enrollment_client.RateLimitedError(
                "Too many attempts"
            ),
        )

        body = json.dumps({
            "enrollment_key": "4F9XK2PBQ7M3N8RT",
        }).encode()
        collector = ResponseCollector()
        _run(handle_enroll(
            _make_scope(), make_receive(body), collector,
        ))

        assert collector.status == 429

    def test_returns_502_for_generic_enrollment_error(self, mocker):
        _set_selected_manager()
        from sethlans_worker_agent import enrollment_client

        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_enrollment"
            "._do_enroll",
            side_effect=enrollment_client.EnrollmentError(
                "Connection refused"
            ),
        )

        body = json.dumps({
            "enrollment_key": "4F9XK2PBQ7M3N8RT",
        }).encode()
        collector = ResponseCollector()
        _run(handle_enroll(
            _make_scope(), make_receive(body), collector,
        ))

        assert collector.status == 502

    def test_appends_enrolled_checkpoint_on_success(self, mocker):
        _set_selected_manager()
        from sethlans_worker_agent.web_ui.setup.handlers_status import (
            get_current_checkpoints,
        )

        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_enrollment"
            "._do_enroll",
            return_value={"status": "ok"},
        )

        body = json.dumps({
            "enrollment_key": "4F9XK2PBQ7M3N8RT",
        }).encode()
        collector = ResponseCollector()
        _run(handle_enroll(
            _make_scope(), make_receive(body), collector,
        ))

        assert "enrolled" in get_current_checkpoints()

    def test_rejects_non_object_body(self):
        body = json.dumps("string").encode()
        collector = ResponseCollector()
        _run(handle_enroll(
            _make_scope(), make_receive(body), collector,
        ))

        assert collector.status == 400
