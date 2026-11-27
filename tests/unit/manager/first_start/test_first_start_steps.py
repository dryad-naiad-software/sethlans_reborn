# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``sethlans_manager.first_start`` (``run``,
``require_first_start``) and ``local_worker``. The username and email
rules (``admin_fields``) are in ``tests/unit/manager/test_admin_fields.py``.

Each step, the settings file, the ``Token`` table, enrollment and the
worker's ``config.json`` are mocked. The trusted, stale-``manager_id``,
deleted-token and manager-only cases with the real database and files are
in ``tests/integration/manager/test_first_start_integration.py``.
"""
import logging
from pathlib import Path
from unittest.mock import call

import pytest
from django.db import DatabaseError, OperationalError

from sethlans_manager import first_start
from sethlans_manager.first_start import local_worker
from sethlans_manager.first_start.common import FirstStartError
from shared.manager_exit_codes import EXIT_FIRST_START_FAILED, EXIT_NO_ADMIN

INI = Path("settings") / "sethlans.ini"


def test_run_steps_in_order(mocker):
    """FR-FS-0: first admin from [first_admin], then from the environment, the admin check, then trust for the mode."""
    steps = mocker.Mock()
    mocker.patch.object(first_start, "apply_first_admin", steps.first_admin)
    mocker.patch.object(first_start, "apply_env_admin", steps.env_admin)
    mocker.patch.object(first_start, "usable_admin_exists", steps.admin_check)
    mocker.patch.object(first_start, "ensure_local_worker_trust", steps.trust)
    steps.admin_check.return_value = True
    mocker.patch.object(first_start.settings_file, "settings_path", return_value=INI)
    mocker.patch.object(first_start.settings_file, "setup_finished", steps.setup_finished)
    steps.setup_finished.return_value = "manager_worker"

    first_start.run()

    assert steps.mock_calls == [
        call.first_admin(INI), call.env_admin(), call.admin_check(),
        call.setup_finished(INI), call.trust("manager_worker"),
    ]


@pytest.mark.parametrize("inactive, reason", [
    pytest.param(False, first_start.NO_ADMIN_REASON, id="no-superuser"),
    pytest.param(True, first_start.INACTIVE_ADMIN_REASON, id="only-inactive-superusers"),
])
def test_no_active_admin_exits_12(mocker, inactive, reason):
    """FR-FS-2a: no active superuser is exit 12 before trust; the reason says when admins exist but none is active."""
    mocker.patch.object(first_start, "apply_first_admin")
    mocker.patch.object(first_start, "apply_env_admin")
    mocker.patch.object(first_start, "usable_admin_exists", return_value=False)
    mocker.patch.object(first_start, "inactive_admin_exists", return_value=inactive)
    trust = mocker.patch.object(first_start, "ensure_local_worker_trust")

    with pytest.raises(FirstStartError) as caught:
        first_start.run(INI)

    assert (caught.value.code, caught.value.reason) == (EXIT_NO_ADMIN, reason)
    trust.assert_not_called()


@pytest.mark.parametrize("error, code, reason", [
    pytest.param(FirstStartError(EXIT_FIRST_START_FAILED, "the hash is bad"), EXIT_FIRST_START_FAILED,
                 "the hash is bad", id="step-failed-11"),
    pytest.param(FirstStartError(EXIT_NO_ADMIN, first_start.NO_ADMIN_REASON), EXIT_NO_ADMIN,
                 first_start.NO_ADMIN_REASON, id="no-admin-12"),
    pytest.param(OperationalError("no such table: auth_user\nDETAIL:  Key (username)=(farmadmin) is secret"),
                 EXIT_FIRST_START_FAILED, "database error: OperationalError: no such table: auth_user",
                 id="database-error-11-without-detail"),
])
def test_require_first_start_exits_with_one_line(mocker, capsys, caplog, error, code, reason):
    """FR-FS-0, FR-FS-2a: a failure is exit 11 or 12 (a DatabaseError is 11, its text cut by short_error before
    DETAIL), one stderr line and one ERROR record."""
    assert isinstance(error, (FirstStartError, DatabaseError))
    mocker.patch.object(first_start, "run", side_effect=error)

    with caplog.at_level(logging.DEBUG, logger=first_start.__name__), pytest.raises(SystemExit) as caught:
        first_start.require_first_start(INI)

    assert caught.value.code == code
    assert capsys.readouterr().err.strip().splitlines() == [f"[ERROR] First start failed: {reason}"]
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r.getMessage() for r in errors] == [f"First start failed (exit {code}): {reason}"]


class TestLocalWorkerTrust:
    @pytest.mark.parametrize("content, error, warns", [
        pytest.param(None, FileNotFoundError(2, "No such file"), False, id="no-config"),
        pytest.param(None, PermissionError(13, "Access is denied"), True, id="unreadable"),
        pytest.param("{not json", None, True, id="corrupt-json"),
        pytest.param('["manager"]', None, False, id="not-an-object"),
    ])
    def test_config_without_a_token_needs_trust(self, mocker, caplog, content, error, warns):
        """FR-FS-3: a missing, unreadable, corrupt or non-object config.json means no token; trust runs again."""
        config_path = mocker.Mock(spec=Path)
        config_path.read_text.side_effect = error
        config_path.read_text.return_value = content
        token = mocker.patch("rest_framework.authtoken.models.Token")

        with caplog.at_level(logging.DEBUG, logger=local_worker.__name__):
            assert local_worker.trust_reason(config_path, "manager-id-1") == "it has no token"

        token.objects.filter.assert_not_called()
        assert any(r.levelno == logging.WARNING for r in caplog.records) is warns

    def test_trust_failure_exits_11_without_the_token(self, mocker, caplog):
        """FR-FS-3: a failure writing the worker's enrollment is exit 11; the token is in neither reason nor log."""
        from workers.services import auto_enroll, filesystem_trust

        mocker.patch.object(local_worker, "trust_reason", return_value="it has no token")
        config_path = Path("worker") / "config.json"
        mocker.patch.object(filesystem_trust, "get_worker_config_path", return_value=config_path)
        mocker.patch.object(auto_enroll, "auto_enroll_local_worker", return_value={
            "api_token": "tok-5e6f7a8b", "cert_fingerprint": "AA:BB", "manager_url": "https://127.0.0.1:8080",
            "manager_id": "manager-id-1",
        })
        write = mocker.patch.object(filesystem_trust, "write_worker_config",
                                    side_effect=PermissionError(13, "Access is denied"))

        with caplog.at_level(logging.DEBUG, logger=local_worker.__name__), pytest.raises(FirstStartError) as caught:
            local_worker.ensure_local_worker_trust("manager_worker")

        assert caught.value.code == EXIT_FIRST_START_FAILED
        assert caught.value.reason.startswith("the local worker could not be trusted: PermissionError")
        assert write.call_args.kwargs["config_path"] == config_path
        assert "tok-5e6f7a8b" not in caught.value.reason and "tok-5e6f7a8b" not in caplog.text

    def test_config_folder_failure_exits_11(self, mocker):
        """FR-FS-3 (review M3): get_worker_config_path raising OSError is the handled exit 11, not a crash."""
        from workers.services import auto_enroll, filesystem_trust

        mocker.patch.object(filesystem_trust, "get_worker_config_path",
                            side_effect=PermissionError(13, "Access is denied"))
        reason = mocker.patch.object(local_worker, "trust_reason")
        enroll = mocker.patch.object(auto_enroll, "auto_enroll_local_worker")

        with pytest.raises(FirstStartError) as caught:
            local_worker.ensure_local_worker_trust("manager_worker")

        assert caught.value.code == EXIT_FIRST_START_FAILED
        assert caught.value.reason == "the local worker could not be trusted: PermissionError: [Errno 13] Access is denied"
        reason.assert_not_called()
        enroll.assert_not_called()
