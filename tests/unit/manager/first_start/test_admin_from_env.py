# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``sethlans_manager.first_start.admin_from_env`` (FR-FS-2).

The password file is a ``mock_open``, the environment a plain dict, and
the user model, password validators and transaction are mocked. The valid,
invalid-password and admin-exists cases with the real database are in
``tests/integration/manager/test_first_start_integration.py``.
"""
import logging

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from sethlans_manager.first_start import admin_from_env as env_admin
from sethlans_manager.first_start.common import FirstStartError
from shared.manager_exit_codes import EXIT_FIRST_START_FAILED, EXIT_NO_ADMIN

SECRET_PATH = "/run/secrets/admin_password"
CAP = env_admin.MAX_PASSWORD_FILE_BYTES


def _open_with(mocker, data=None, error=None):
    handle = mocker.mock_open(read_data=data)
    if error is not None:
        handle.side_effect = error
    return mocker.patch.object(env_admin, "open", handle, create=True)


class TestReadPasswordFile:
    @pytest.mark.parametrize("data, expected", [
        pytest.param(b"pw\r\n", "pw", id="crlf-dropped"),
        pytest.param(b"pw\n\n", "pw\n", id="only-one-line-end-dropped"),
        pytest.param(b"x" * CAP, "x" * CAP, id="exactly-64-KiB"),
    ])
    def test_password_from_file(self, mocker, data, expected):
        """FR-FS-2, FR-DK-3: UTF-8, one trailing line end dropped, up to 64 KiB accepted."""
        opened = _open_with(mocker, data)

        assert env_admin._read_password_file(SECRET_PATH) == expected
        opened.assert_called_once_with(SECRET_PATH, "rb")

    @pytest.mark.parametrize("data, error, reason", [
        pytest.param(b"x" * (CAP + 1), None, f"is larger than {CAP} bytes", id="over-64-KiB"),
        pytest.param(b"secret-\xff\xfe", None, "is not UTF-8 text", id="not-utf8"),
        pytest.param(None, PermissionError(13, "Permission denied"), "cannot be read: Permission denied",
                     id="unreadable"),
    ])
    def test_unusable_file_exits_11(self, mocker, data, error, reason):
        """FR-FS-2, FR-DK-3: a file too big, not UTF-8 or unreadable is exit 11 naming the variable, not the content."""
        _open_with(mocker, data, error)

        with pytest.raises(FirstStartError) as caught:
            env_admin._read_password_file(SECRET_PATH)

        assert caught.value.code == EXIT_FIRST_START_FAILED
        assert caught.value.reason.startswith(f"{env_admin.PASSWORD_FILE_ENV} ({SECRET_PATH})")
        assert reason in caught.value.reason
        assert "secret-" not in caught.value.reason


def test_password_file_wins_over_plain_password(mocker, caplog):
    """FR-FS-2 (SR-F8): with both variables set the file is used and one WARNING says so, without either password."""
    read = mocker.patch.object(env_admin, "_read_password_file", return_value="file-secret-1a2b")
    values = {env_admin.PASSWORD_ENV: "plain-secret-3c4d", env_admin.PASSWORD_FILE_ENV: SECRET_PATH}

    with caplog.at_level(logging.DEBUG, logger=env_admin.__name__):
        assert env_admin._password(values) == "file-secret-1a2b"

    read.assert_called_once_with(SECRET_PATH)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "using the file" in warnings[0].getMessage()
    assert "plain-secret-3c4d" not in caplog.text and "file-secret-1a2b" not in caplog.text


@pytest.mark.parametrize("environ, reason", [
    pytest.param({env_admin.USERNAME_ENV: "dockadmin", env_admin.PASSWORD_ENV: ""},
                 f"{env_admin.USERNAME_ENV} is set without", id="username-without-password"),
    pytest.param({env_admin.PASSWORD_FILE_ENV: SECRET_PATH, env_admin.EMAIL_ENV: "dock@example.com"},
                 f"is set without {env_admin.USERNAME_ENV}", id="password-without-username"),
])
def test_half_a_pair_exits_11(mocker, environ, reason):
    """FR-FS-2: a username with no (or an empty) password, or a password with no username, is exit 11; all removed."""
    mocker.patch.object(env_admin, "usable_admin_exists", return_value=False)
    create = mocker.patch.object(env_admin, "_create")

    with pytest.raises(FirstStartError) as caught:
        env_admin.apply_env_admin(environ)

    assert caught.value.code == EXIT_FIRST_START_FAILED
    assert reason in caught.value.reason
    assert environ == {}
    create.assert_not_called()


def test_problems_are_username_email_and_password_together(mocker):
    """FR-FS-2: the username's, the email's (named by its variable) and the password's problems are all reported;
    the validators get the unsaved user."""
    email = mocker.patch.object(env_admin, "email_problems", return_value=["email problem"])
    validate = mocker.patch.object(env_admin, "validate_password",
                                   side_effect=ValidationError(["password problem"]))
    model = mocker.patch.object(env_admin, "get_user_model").return_value

    problems = env_admin._problems(["username problem"], "dockadmin", "not-an-email", "Admin-Secret-7e8f")

    assert problems == ["username problem", "email problem", "password problem"]
    email.assert_called_once_with("not-an-email", env_admin.EMAIL_ENV)
    model.assert_called_once_with(username="dockadmin", email="not-an-email")
    validate.assert_called_once_with("Admin-Secret-7e8f", user=model.return_value)


@pytest.mark.parametrize("inactive_admin, reason", [
    pytest.param(False, "already exists and is not an admin", id="not-an-admin"),
    pytest.param(True, "already exists as an admin that is not active; run Reset setup and tick "
                       "'Replace its password?' to make it active", id="inactive-admin"),
])
def test_username_taken_exits_12(mocker, inactive_admin, reason):
    """FR-FS-2: a taken username (IntegrityError) is exit 12 without the password; the text says when the
    account is an inactive admin."""
    mocker.patch.object(env_admin, "transaction")
    objects = mocker.patch.object(env_admin, "get_user_model").return_value.objects
    objects.create_superuser.side_effect = IntegrityError("UNIQUE constraint failed")
    objects.filter.return_value.exists.return_value = inactive_admin

    with pytest.raises(FirstStartError) as caught:
        env_admin._create("artist", "", "Admin-Secret-7e8f")

    assert caught.value.code == EXIT_NO_ADMIN
    assert caught.value.reason == f"the account 'artist' from {env_admin.USERNAME_ENV} {reason}"
    assert "Admin-Secret-7e8f" not in caught.value.reason
    objects.create_superuser.assert_called_once_with("artist", "", "Admin-Secret-7e8f")
    objects.filter.assert_called_once_with(username="artist", is_superuser=True, is_active=False)
