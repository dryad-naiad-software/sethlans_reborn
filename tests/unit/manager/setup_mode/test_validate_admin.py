# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""The validation table: the Admin section (keep, new admin, replace password, password rules)."""
import pytest

from sethlans_manager.admin_fields import clean_username
from sethlans_manager.setup_mode import db_probe
from tests.unit.manager.setup_mode._helpers import FAKE_HASH, GOOD_PASSWORD, answers

EXISTS = "This account exists. Replace its password?"
NONE_TO_KEEP = "The chosen database has no admin account to keep."


def _admin(check, tmp_path, **admin):
    return check(answers(tmp_path, admin=admin))


def _new(**over):
    admin = {"username": "boss", "email": "boss@example.com", "password": GOOD_PASSWORD}
    admin.update(over)
    return admin


class TestKeepExisting:

    def test_keep_with_a_superuser_makes_no_admin(self, check, tmp_path, probe, hasher):
        probe.return_value = db_probe.AdminAccounts(True, None)
        errors, plan = _admin(check, tmp_path, keep_existing=True)
        assert errors == {} and plan.first_admin is None
        hasher.assert_not_called()

    def test_keep_without_accounts_is_refused(self, check, tmp_path):
        errors, plan = _admin(check, tmp_path, keep_existing=True)
        assert plan is None
        assert errors == {"admin.keep_existing": [NONE_TO_KEEP]}

    def test_keep_after_a_database_failure_reports_only_the_database(self, check, tmp_path, probe):
        probe.side_effect = db_probe.ProbeError("timeout")
        errors, _plan = _admin(check, tmp_path, keep_existing=True)
        assert list(errors) == ["database"]

    def test_keep_must_be_a_boolean(self, check, tmp_path):
        errors, _plan = _admin(check, tmp_path, keep_existing="yes", **_new())
        assert errors == {"admin.keep_existing": ["Must be true or false."]}


class TestNewAdmin:

    def test_first_admin_keys(self, check, tmp_path, hasher):
        errors, plan = _admin(check, tmp_path, **_new())
        assert errors == {}
        assert plan.first_admin == {"username": "boss", "email": "boss@example.com",
                                    "password_hash": FAKE_HASH, "replace_password": "false"}
        hasher.assert_called_once_with(GOOD_PASSWORD)

    def test_plan_carries_no_plain_password(self, check, tmp_path):
        _errors, plan = _admin(check, tmp_path, **_new())
        assert GOOD_PASSWORD not in repr(plan)

    @pytest.mark.parametrize("username, message", [
        ("", "The username is required."),
        ("a" * 151, "The username is longer than 150 characters."),
        ("bad name!", "Enter a valid username. This value may contain only letters, numbers, and @/./+/-/_ "
                      "characters."),
        (12, "The username must be text."),
    ])
    def test_username_errors(self, check, tmp_path, username, message):
        errors, _plan = _admin(check, tmp_path, **_new(username=username))
        assert errors == {"admin.username": [message]}

    @pytest.mark.parametrize("username", ["a", "a" * 150, "first.last+x@site-1_b", "émile"])
    def test_usernames_accepted(self, check, tmp_path, username):
        errors, plan = _admin(check, tmp_path, **_new(username=username, email=""))
        assert errors == {} and plan.first_admin["username"] == username

    @pytest.mark.parametrize("email", ["", None])
    def test_email_may_be_empty(self, check, tmp_path, email):
        errors, plan = _admin(check, tmp_path, **_new(email=email))
        assert errors == {} and plan.first_admin["email"] == ""

    def test_invalid_email(self, check, tmp_path):
        errors, _plan = _admin(check, tmp_path, **_new(email="not-an-email"))
        assert errors == {"admin.email": ["Enter a valid email address or leave it empty."]}

    @pytest.mark.parametrize("password, message", [
        ("", "A password is required."),
        (None, "A password is required."),
        (12345678, "A password is required."),
        ("password", "This password is too common."),
        ("Ab1-x", "This password is too short. It must contain at least 8 characters."),
        ("8417305926", "This password is entirely numeric."),
        ("x" * 4097, "The password is longer than 4096 characters."),
    ])
    def test_password_errors(self, check, tmp_path, password, message, hasher):
        errors, plan = _admin(check, tmp_path, **_new(password=password))
        assert plan is None
        assert message in errors["admin.password"]
        hasher.assert_not_called()

    def test_password_similar_to_the_email_is_refused(self, check, tmp_path):
        admin = _new(email="quartzfalcon@example.com", password="quartzfalcon@example.com")
        errors, _plan = _admin(check, tmp_path, **admin)
        assert errors == {"admin.password": ["The password is too similar to the email address."]}

    def test_password_similar_to_the_username_is_refused(self, check, tmp_path):
        errors, _plan = _admin(check, tmp_path, **_new(username="quartzfalcon", email="", password="quartzfalcon1"))
        assert errors == {"admin.password": ["The password is too similar to the username."]}

    def test_any_other_error_means_no_hash(self, check, tmp_path, hasher):
        check(answers(tmp_path, mode="worker"))
        hasher.assert_not_called()

    def test_admin_not_an_object(self, check, tmp_path):
        errors, _plan = check(answers(tmp_path, admin="boss"))
        assert errors["admin"] == ["Must be an object."]


class TestExistingUsername:

    def test_existing_username_without_replace_is_refused(self, check, tmp_path, probe):
        probe.return_value = db_probe.AdminAccounts(True, True)
        errors, plan = _admin(check, tmp_path, **_new())
        assert plan is None
        assert errors == {"admin.username": [EXISTS]}

    def test_existing_username_with_replace(self, check, tmp_path, probe):
        probe.return_value = db_probe.AdminAccounts(True, True)
        errors, plan = _admin(check, tmp_path, **_new(replace_password=True))
        assert errors == {} and plan.first_admin["replace_password"] == "true"

    def test_replace_for_a_new_username_writes_false(self, check, tmp_path, probe):
        probe.return_value = db_probe.AdminAccounts(True, False)
        _errors, plan = _admin(check, tmp_path, **_new(replace_password=True))
        assert plan.first_admin["replace_password"] == "false"

    def test_replace_must_be_a_boolean(self, check, tmp_path):
        errors, _plan = _admin(check, tmp_path, **_new(replace_password="true"))
        assert errors == {"admin.replace_password": ["Must be true or false."]}

    def test_username_is_probed_and_written_as_the_first_start_reads_it(self, check, tmp_path, probe):
        """FR-WZ-5, FR-FS-1 (review M7): the database probe and [first_admin] get the name clean_username makes
        (stripped, NFKC), so a name setup accepts is never changed or refused at the first start."""
        raw = "  ｂoss  "
        errors, plan = _admin(check, tmp_path, **_new(username=raw))
        assert errors == {}
        assert probe.call_args.args[1] == plan.first_admin["username"] == clean_username(raw)[0] == "boss"
