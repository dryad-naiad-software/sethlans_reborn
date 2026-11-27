# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for ``sethlans_manager.first_start.admin_from_hash`` (FR-FS-1).

Django's hasher functions, the user model, the transaction and the
settings file are mocked; no hash is computed and no database is touched.
The create, retry, replace and clash cases with a real hash and database
are in ``tests/integration/manager/test_first_start_integration.py``.
"""
import configparser
import logging
from pathlib import Path

import pytest
from django.db import IntegrityError

from sethlans_manager.first_start import admin_from_hash
from sethlans_manager.first_start.common import FirstStartError
from shared.manager_exit_codes import EXIT_FIRST_START_FAILED, EXIT_NO_ADMIN

SECTION = admin_from_hash.SECTION
GOOD_HASH = "pbkdf2_sha256$1000000$c2FsdHNhbHQ$ZGlnZXN0ZGlnZXN0"
INI = Path("settings") / "sethlans.ini"


def _parser(**values):
    parser = configparser.RawConfigParser(interpolation=None)
    parser.read_dict({SECTION: values})
    return parser


DECODED = {"salt": "c2FsdHNhbHQ", "hash": "ZGlnZXN0ZGlnZXN0"}


class TestCheckHash:
    @pytest.mark.parametrize("password_hash, case, reason", [
        pytest.param(GOOD_HASH, {}, None, id="valid"),
        pytest.param("pbkdf2_sha1$1000000$salt$digest", {}, "does not start with pbkdf2_sha256$", id="wrong-prefix"),
        pytest.param(GOOD_HASH, {"decode": ValueError("bad")}, "unknown format", id="decode-fails"),
        pytest.param(GOOD_HASH, {"default": "argon2"}, "not made with the default hasher", id="other-algorithm"),
        pytest.param(GOOD_HASH, {"usable": False}, "is not usable", id="not-usable"),
        pytest.param(GOOD_HASH, {"decode": {**DECODED, "salt": ""}}, "empty salt or hash", id="empty-salt"),
        pytest.param(GOOD_HASH, {"decode": {**DECODED, "hash": ""}}, "empty salt or hash", id="empty-hash"),
        pytest.param(GOOD_HASH, {"must_update": True}, "does not match this manager's hasher settings",
                     id="must-update"),
    ])
    def test_hash_checks(self, mocker, password_hash, case, reason):
        """FR-FS-1, D6: exact prefix, decodable, default hasher's algorithm, usable, non-empty salt and hash,
        no update needed (same iterations and salt size); else exit 11 without the hash."""
        hasher = mocker.Mock(algorithm="pbkdf2_sha256")
        decode = case.get("decode", DECODED)
        hasher.decode.side_effect = decode if isinstance(decode, Exception) else None
        hasher.decode.return_value = decode
        hasher.must_update.return_value = case.get("must_update", False)
        identify = mocker.patch.object(admin_from_hash, "identify_hasher", return_value=hasher)
        default = mocker.Mock(algorithm=case.get("default", "pbkdf2_sha256"))
        mocker.patch.object(admin_from_hash, "get_hasher", return_value=default)
        mocker.patch.object(admin_from_hash, "is_password_usable", return_value=case.get("usable", True))

        if reason is None:
            admin_from_hash.check_hash(password_hash)
            hasher.decode.assert_called_once_with(password_hash)
            hasher.must_update.assert_called_once_with(password_hash)
            return
        with pytest.raises(FirstStartError) as caught:
            admin_from_hash.check_hash(password_hash)

        assert caught.value.code == EXIT_FIRST_START_FAILED
        assert f"[{SECTION}]" in caught.value.reason and reason in caught.value.reason
        assert password_hash not in caught.value.reason
        if reason.startswith("does not start"):
            identify.assert_not_called()


class TestReadFirstAdmin:
    @pytest.mark.parametrize("values, problems, reason", [
        pytest.param({"username": "bad name", "replace_password": "false"}, ["Enter a valid username."],
                     "Enter a valid username.", id="bad-username"),
        pytest.param({"username": "farmadmin", "replace_password": "yes"}, [],
                     "replace_password must be true or false", id="replace-yes"),
        pytest.param({"username": "farmadmin", "replace_password": ""}, [],
                     "replace_password must be true or false", id="replace-empty"),
    ])
    def test_section_rejected_before_the_hash(self, mocker, values, problems, reason):
        """FR-FS-1, OQ-2: a bad username or a replace_password not true/false is exit 11; the hash is not looked at."""
        clean = mocker.patch.object(admin_from_hash, "clean_username", side_effect=lambda raw: (raw, problems))
        check_hash = mocker.patch.object(admin_from_hash, "check_hash")

        with pytest.raises(FirstStartError) as caught:
            admin_from_hash.read_first_admin(_parser(password_hash=GOOD_HASH, **values))

        assert caught.value.code == EXIT_FIRST_START_FAILED
        assert reason in caught.value.reason
        clean.assert_called_once_with(values["username"])
        check_hash.assert_not_called()


class TestExistingAccount:
    def test_account_gone_after_the_clash_exits_11(self, mocker):
        """FR-FS-1: IntegrityError but no account under that username (removed meanwhile) is exit 11 naming the
        error type; no digest is recorded."""
        mocker.patch.object(admin_from_hash, "transaction")
        row = mocker.patch.object(admin_from_hash, "_settings_row").return_value
        row.first_admin_applied = ""
        objects = mocker.patch.object(admin_from_hash, "get_user_model").return_value.objects
        objects.create_superuser.side_effect = IntegrityError("UNIQUE constraint failed")
        objects.select_for_update.return_value.filter.return_value.first.return_value = None
        admin = {"username": "farmadmin", "email": "", "password_hash": GOOD_HASH, "replace_password": True}

        with pytest.raises(FirstStartError) as caught:
            admin_from_hash.create_first_admin(admin)

        assert caught.value.code == EXIT_FIRST_START_FAILED
        assert caught.value.reason == "the admin account 'farmadmin' could not be created (IntegrityError)"
        objects.select_for_update.return_value.filter.assert_called_once_with(username="farmadmin")
        row.save.assert_not_called()

    def test_worker_service_account_is_never_made_the_admin(self, mocker):
        """FR-FS-1: replace_password = true on a worker's service account is exit 12; the account is not changed."""
        user = mocker.Mock(password="pbkdf2_sha256$other", is_superuser=False, is_staff=False, is_active=True)
        objects = mocker.patch.object(admin_from_hash, "get_user_model").return_value.objects
        objects.select_for_update.return_value.filter.return_value.first.return_value = user
        is_worker = mocker.patch.object(admin_from_hash, "_is_worker_account", return_value=True)
        admin = {"username": "worker-node1", "email": "", "password_hash": GOOD_HASH, "replace_password": True}

        with pytest.raises(FirstStartError) as caught:
            admin_from_hash._existing_account(admin, "IntegrityError")

        assert caught.value.code == EXIT_NO_ADMIN
        assert "'worker-node1' is a worker's service account" in caught.value.reason
        is_worker.assert_called_once_with(user)
        user.save.assert_not_called()
        assert (user.password, user.is_superuser, user.is_staff) == ("pbkdf2_sha256$other", False, False)


class TestApplyFirstAdmin:
    def test_unreadable_settings_file_exits_11(self, mocker):
        """FR-FS-1: a settings file that cannot be read is exit 11 before any account work."""
        error = admin_from_hash.settings_file.SettingsFileError("not owned by the current user")
        mocker.patch.object(admin_from_hash.settings_file, "read_settings", side_effect=error)
        create = mocker.patch.object(admin_from_hash, "create_first_admin")

        with pytest.raises(FirstStartError) as caught:
            admin_from_hash.apply_first_admin(INI)

        assert caught.value.code == EXIT_FIRST_START_FAILED
        assert caught.value.reason == "the settings file cannot be read: not owned by the current user"
        create.assert_not_called()

    def test_section_removal_failure_warns_and_the_start_goes_on(self, mocker, caplog):
        """FR-FS-1 (DA-F5): an OSError removing [first_admin] is one WARNING; the outcome is still returned."""
        parser = _parser(username="farmadmin")
        mocker.patch.object(admin_from_hash.settings_file, "read_settings", return_value=parser)
        admin = {"username": "farmadmin", "password_hash": GOOD_HASH}
        mocker.patch.object(admin_from_hash, "read_first_admin", return_value=admin)
        create = mocker.patch.object(admin_from_hash, "create_first_admin", return_value="created")
        remove = mocker.patch.object(admin_from_hash.settings_file, "remove_section",
                                     side_effect=PermissionError(13, "Access is denied"))

        with caplog.at_level(logging.DEBUG, logger=admin_from_hash.__name__):
            assert admin_from_hash.apply_first_admin(INI) == "created"

        create.assert_called_once_with(admin)
        remove.assert_called_once_with(SECTION, INI)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "PermissionError" in warnings[0].getMessage()
        assert GOOD_HASH not in caplog.text
