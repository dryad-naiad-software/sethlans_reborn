# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``web_ui/setup/handlers_password.py``.

Covers handle_set_worker_password ASGI handler with mocked
auth.set_password.  Module state is reset by the autouse fixtures
in ``conftest.py``.
"""

import asyncio
import json

from sethlans_worker_agent.web_ui.setup.handlers_password import (
    handle_set_worker_password,
    _MIN_PASSWORD_LENGTH,
)

from tests.unit.worker._asgi_helpers import (
    make_scope,
    make_receive,
    ResponseCollector,
)


def _run(coro):
    return asyncio.run(coro)


def _make_scope():
    return make_scope(
        method='POST', path='/api/setup/worker-password/',
    )


class TestHandleSetWorkerPassword:
    def test_calls_set_password_on_valid_input(self, mocker):
        mock_set_pw = mocker.patch(
            "sethlans_worker_agent.web_ui.auth.set_password",
        )

        body = json.dumps({
            "password": "securepassword123",
        }).encode()
        collector = ResponseCollector()
        _run(handle_set_worker_password(
            _make_scope(), make_receive(body), collector,
        ))

        assert collector.status == 200
        assert collector.json["status"] == "ok"
        mock_set_pw.assert_called_once_with("securepassword123")

    def test_rejects_short_password(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.auth.set_password",
        )

        short_pw = "a" * (_MIN_PASSWORD_LENGTH - 1)
        body = json.dumps({"password": short_pw}).encode()
        collector = ResponseCollector()
        _run(handle_set_worker_password(
            _make_scope(), make_receive(body), collector,
        ))

        assert collector.status == 400
        assert "at least" in collector.json["error"].lower()

    def test_rejects_empty_password(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.auth.set_password",
        )

        body = json.dumps({"password": ""}).encode()
        collector = ResponseCollector()
        _run(handle_set_worker_password(
            _make_scope(), make_receive(body), collector,
        ))

        assert collector.status == 400

    def test_rejects_missing_password_field(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.auth.set_password",
        )

        body = json.dumps({}).encode()
        collector = ResponseCollector()
        _run(handle_set_worker_password(
            _make_scope(), make_receive(body), collector,
        ))

        assert collector.status == 400

    def test_appends_checkpoint(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.auth.set_password",
        )
        from sethlans_worker_agent.web_ui.setup.handlers_status import (
            get_current_checkpoints,
        )

        body = json.dumps({
            "password": "goodpassword",
        }).encode()
        collector = ResponseCollector()
        _run(handle_set_worker_password(
            _make_scope(), make_receive(body), collector,
        ))

        assert "password_set" in get_current_checkpoints()

    def test_rejects_non_object_body(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.auth.set_password",
        )

        body = json.dumps([1, 2, 3]).encode()
        collector = ResponseCollector()
        _run(handle_set_worker_password(
            _make_scope(), make_receive(body), collector,
        ))

        assert collector.status == 400
        assert "JSON object" in collector.json["error"]

    def test_exactly_min_length_is_accepted(self, mocker):
        mock_set_pw = mocker.patch(
            "sethlans_worker_agent.web_ui.auth.set_password",
        )

        pw = "a" * _MIN_PASSWORD_LENGTH
        body = json.dumps({"password": pw}).encode()
        collector = ResponseCollector()
        _run(handle_set_worker_password(
            _make_scope(), make_receive(body), collector,
        ))

        assert collector.status == 200
        mock_set_pw.assert_called_once_with(pw)
