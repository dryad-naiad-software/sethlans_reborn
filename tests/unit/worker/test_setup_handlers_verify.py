# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Unit tests for ``web_ui/setup/handlers_verify.py``.

Phase 4c of the Waitress migration: the handler is sync WSGI and
guarded by :func:`setup_mutation_lock`.  Tests drive the handler
directly via a WSGI ``environ`` + ``start_response`` capture (no
async plumbing).  Module state is reset by the autouse fixtures
in ``conftest.py``.
"""

import io
import json
import threading

from sethlans_worker_agent.web_ui.setup.handlers_verify import (
    handle_verify,
)
from sethlans_worker_agent.web_ui.setup import lock as lock_module

from tests.unit.worker._wsgi_helpers import StartResponseCapture


def _make_environ() -> dict:
    return {
        'REQUEST_METHOD': 'POST',
        'PATH_INFO': '/api/setup/verify/',
        'CONTENT_LENGTH': '0',
        'wsgi.input': io.BytesIO(b''),
    }


def _call(cap):
    return b''.join(handle_verify(_make_environ(), cap))


def _status_code(cap: StartResponseCapture) -> int:
    return int((cap.status or '').split(' ', 1)[0])


def _mock_all_passing(mocker, blenders=None):
    """Set up mocks so all required checks pass."""
    mocker.patch(
        "sethlans_worker_agent.web_ui.setup.handlers_verify"
        ".config_store.get",
        side_effect=lambda key, default=None: {
            "manager.api_token": "tok-123",
            "manager.cert_fingerprint": "ff" * 32,
        }.get(key, default),
    )
    mocker.patch(
        "sethlans_worker_agent.web_ui.setup.handlers_verify"
        ".config_store.get_data_dir",
        return_value="/fake/dir",
    )
    mocker.patch(
        "sethlans_worker_agent.web_ui.setup.handlers_verify"
        ".config_store.set",
    )
    mocker.patch(
        "sethlans_worker_agent.web_ui.auth.is_password_configured",
        return_value=True,
    )
    mock_tm = mocker.MagicMock()
    mock_tm.scan_for_local_blenders.return_value = (
        blenders if blenders is not None else ["/usr/bin/blender"]
    )
    mocker.patch(
        "sethlans_worker_agent.tool_manager.tool_manager_instance",
        mock_tm,
    )
    mock_create = mocker.patch(
        "sethlans_worker_agent.web_ui.setup.handlers_verify"
        ".create_sentinel",
    )
    mock_gate = mocker.patch(
        "sethlans_worker_agent.web_ui.setup.handlers_verify"
        ".mark_setup_complete",
    )
    return mock_create, mock_gate


class TestHandleVerify:
    def test_all_passed_when_enrolled_and_password_set(self, mocker):
        mock_create, mock_gate = _mock_all_passing(mocker)

        cap = StartResponseCapture()
        out = _call(cap)

        assert _status_code(cap) == 200
        body = json.loads(out)
        assert body["all_passed"] is True
        assert len(body["checks"]) == 3
        mock_create.assert_called_once()
        mock_gate.assert_called_once()

    def test_writes_sentinel_and_flips_gate(self, mocker):
        mock_create, mock_gate = _mock_all_passing(mocker)

        cap = StartResponseCapture()
        _call(cap)

        mock_create.assert_called_once()
        # Verify sentinel args include topology and checkpoints
        call_args = mock_create.call_args
        assert call_args[0][0] == "/fake/dir"  # data_dir
        assert "verified" in call_args[0][2]  # checkpoints
        mock_gate.assert_called_once()

    def test_marks_wizard_complete_in_config_store(self, mocker):
        _mock_all_passing(mocker)
        mock_set = mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_verify"
            ".config_store.set",
        )

        cap = StartResponseCapture()
        _call(cap)

        mock_set.assert_called_once_with(
            "enrollment.wizard_complete", True,
        )

    def test_all_passed_false_when_enrollment_missing(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_verify"
            ".config_store.get",
            return_value=None,
        )
        mocker.patch(
            "sethlans_worker_agent.web_ui.auth.is_password_configured",
            return_value=True,
        )
        mock_tm = mocker.MagicMock()
        mock_tm.scan_for_local_blenders.return_value = []
        mocker.patch(
            "sethlans_worker_agent.tool_manager.tool_manager_instance",
            mock_tm,
        )
        mock_create = mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_verify"
            ".create_sentinel",
        )

        cap = StartResponseCapture()
        out = _call(cap)

        body = json.loads(out)
        assert body["all_passed"] is False
        enrollment_check = next(
            c for c in body["checks"] if c["name"] == "enrollment"
        )
        assert enrollment_check["passed"] is False
        mock_create.assert_not_called()

    def test_blender_is_optional_not_required_for_pass(self, mocker):
        """Blender not installed should still allow all_passed=True."""
        mock_create, mock_gate = _mock_all_passing(
            mocker, blenders=[],
        )

        cap = StartResponseCapture()
        out = _call(cap)

        body = json.loads(out)
        assert body["all_passed"] is True
        blender_check = next(
            c for c in body["checks"] if c["name"] == "blender"
        )
        assert blender_check["passed"] is False
        assert blender_check.get("optional") is True
        mock_create.assert_called_once()
        mock_gate.assert_called_once()

    def test_all_passed_false_when_password_missing(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_verify"
            ".config_store.get",
            side_effect=lambda key, default=None: {
                "manager.api_token": "tok",
                "manager.cert_fingerprint": "ff" * 32,
            }.get(key, default),
        )
        mocker.patch(
            "sethlans_worker_agent.web_ui.auth.is_password_configured",
            return_value=False,
        )
        mock_tm = mocker.MagicMock()
        mock_tm.scan_for_local_blenders.return_value = []
        mocker.patch(
            "sethlans_worker_agent.tool_manager.tool_manager_instance",
            mock_tm,
        )
        mock_create = mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_verify"
            ".create_sentinel",
        )

        cap = StartResponseCapture()
        out = _call(cap)

        body = json.loads(out)
        assert body["all_passed"] is False
        pw_check = next(
            c for c in body["checks"] if c["name"] == "password"
        )
        assert pw_check["passed"] is False
        mock_create.assert_not_called()

    def test_does_not_write_sentinel_when_checks_fail(self, mocker):
        mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_verify"
            ".config_store.get",
            return_value=None,
        )
        mocker.patch(
            "sethlans_worker_agent.web_ui.auth.is_password_configured",
            return_value=False,
        )
        mock_tm = mocker.MagicMock()
        mock_tm.scan_for_local_blenders.return_value = []
        mocker.patch(
            "sethlans_worker_agent.tool_manager.tool_manager_instance",
            mock_tm,
        )
        mock_create = mocker.patch(
            "sethlans_worker_agent.web_ui.setup.handlers_verify"
            ".create_sentinel",
        )

        cap = StartResponseCapture()
        _call(cap)

        mock_create.assert_not_called()

    def test_contention_returns_409(self, mocker):
        """Hold the lock in a background thread, then hit the handler.

        The second caller must see 409 without touching any of the
        check subsystems -- we mock nothing beyond what's needed.
        """
        lock_held = threading.Event()
        release_holder = threading.Event()

        def holder():
            with lock_module.setup_mutation_lock() as acquired:
                assert acquired
                lock_held.set()
                release_holder.wait(timeout=5)

        t = threading.Thread(target=holder)
        t.start()
        try:
            assert lock_held.wait(timeout=5)

            cap = StartResponseCapture()
            out = _call(cap)

            assert _status_code(cap) == 409
            payload = json.loads(out)
            assert payload == {
                "error": "Setup mutation in progress; retry after "
                         "current operation completes.",
            }
        finally:
            release_holder.set()
            t.join(timeout=5)
