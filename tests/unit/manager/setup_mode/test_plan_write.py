# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``plan.write_settings`` and ``submit.apply_answers``: what ``sethlans.ini`` holds after a save."""
import datetime
from pathlib import Path

import pytest

from sethlans_manager.setup_mode import plan as plan_module
from sethlans_manager.setup_mode import submit
from sethlans_manager.setup_mode.plan import Plan
from shared import data_folder, owner_only_acl
from shared.settings_file import read_settings
from tests.unit.manager.setup_mode._helpers import FAKE_HASH, GOOD_PASSWORD, answers

OLD = """[setup]
token = wizard-token
session_id = wizard-session
note = kept

[server]
host = 10.0.0.1
port = 8080
waitress_loopback_port_public = 18090

[database]
engine = postgresql
name = old
password = old-db-pw
conn_max_age = 120

[first_admin]
username = old
password_hash = old-hash

[enrollment]
key = KEEPME
"""


def _plan(root, first_admin=None, **over):
    values = dict(mode="manager", data_root=Path(root), server_host="0.0.0.0", server_port=9443,
                  database={"engine": "sqlite"}, first_admin=first_admin)
    values.update(over)
    return Plan(**values)


ADMIN = {"username": "boss", "email": "boss@example.com", "password_hash": FAKE_HASH, "replace_password": "false"}


@pytest.fixture
def old_file(ini_path):
    ini_path.parent.mkdir(parents=True)
    ini_path.write_text(OLD)
    return ini_path


class TestWriteSettings:

    def test_sections_after_a_save(self, old_file, tmp_path):
        plan_module.write_settings(_plan(tmp_path / "data", ADMIN))
        parser = read_settings(old_file)
        assert dict(parser["database"]) == {"engine": "sqlite", "conn_max_age": "120"}
        assert dict(parser["server"]) == {"host": "0.0.0.0", "port": "9443", "waitress_loopback_port_public": "18090"}
        assert dict(parser["paths"]) == {"data_dir": str(tmp_path / "data")}
        assert dict(parser["first_admin"]) == ADMIN
        assert dict(parser["enrollment"]) == {"key": "KEEPME"}

    def test_setup_section(self, old_file, tmp_path):
        plan_module.write_settings(_plan(tmp_path / "data", ADMIN))
        setup = dict(read_settings(old_file)["setup"])
        assert set(setup) == {"note", "version", "mode", "completed_at"}
        assert (setup["version"], setup["mode"], setup["note"]) == ("1", "manager", "kept")
        stamp = datetime.datetime.fromisoformat(setup["completed_at"])
        assert stamp.utcoffset() == datetime.timedelta(0)
        assert abs(datetime.datetime.now(datetime.timezone.utc) - stamp) < datetime.timedelta(minutes=5)

    def test_keeping_admins_removes_first_admin(self, old_file, tmp_path):
        plan_module.write_settings(_plan(tmp_path / "data", None))
        assert not read_settings(old_file).has_section("first_admin")

    def test_new_file_is_owner_only(self, ini_path, tmp_path):
        assert plan_module.write_settings(_plan(tmp_path / "data", ADMIN)) == ini_path
        assert owner_only_acl.verify_owner_only(ini_path)

    def test_external_database_keys(self, ini_path, tmp_path):
        database = {"engine": "mysql", "name": "s", "host": "db", "port": "3306", "user": "u",
                    "password": "db-pw", "tls": "true", "tls_ca_file": str(tmp_path / "ca.pem")}
        plan_module.write_settings(_plan(tmp_path / "data", ADMIN, database=database))
        assert dict(read_settings(ini_path)["database"]) == database

    def test_value_with_a_newline_is_refused(self, ini_path, tmp_path):
        with pytest.raises(ValueError):
            plan_module.write_settings(_plan(tmp_path / "data", dict(ADMIN, username="a\nb")))
        assert not ini_path.exists()


@pytest.fixture
def saving(fake_net, mocker):
    """``apply_answers`` with the database test replaced (SQLite, no accounts)."""
    from sethlans_manager.setup_mode import db_probe, validate

    mocker.patch.object(validate.db_probe, "check_database", return_value=db_probe.AdminAccounts(False, False))
    mocker.patch.object(data_folder, "free_bytes", return_value=500 * 1000 ** 3)


class TestApplyAnswers:

    def test_saved_file_has_a_hash_and_no_plain_password(self, saving, ctx, ini_path, tmp_path, caplog):
        caplog.set_level("DEBUG")
        status, body = submit.apply_answers(answers(tmp_path), ctx, "setuphost")
        assert status == 200
        raw = ini_path.read_bytes()
        assert GOOD_PASSWORD.encode() not in raw
        admin = read_settings(ini_path)["first_admin"]
        assert admin["password_hash"].startswith("pbkdf2_sha256$1000000$")
        assert admin["replace_password"] == "false"
        assert GOOD_PASSWORD not in caplog.text and GOOD_PASSWORD not in repr(body)

    def test_data_root_is_prepared_owner_only(self, saving, ctx, tmp_path, hasher):
        submit.apply_answers(answers(tmp_path), ctx, "setuphost")
        root = tmp_path / "sethlans-data"
        for folder in (root, root / "manager", root / "worker"):
            assert folder.is_dir() and owner_only_acl.verify_owner_only(folder)
        assert (root / ".sethlans-data").is_file()

    def test_finish_body(self, saving, ctx, tmp_path, hasher):
        status, body = submit.apply_answers(answers(tmp_path), ctx, "setuphost")
        assert (status, body) == (200, {
            "ok": True, "mode": "manager", "restarting": True, "url": "https://setuphost:18444/",
            "port_changed": True, "first_admin": "boss", "warnings": [], "leftovers": []})

    def test_validation_errors_write_nothing(self, saving, ctx, ini_path, tmp_path):
        status, body = submit.apply_answers(answers(tmp_path, mode="bogus"), ctx, "h")
        assert status == 400 and list(body) == ["mode"]
        assert not ini_path.exists() and not (tmp_path / "sethlans-data").exists()

    def test_data_folder_failure_is_400(self, saving, ctx, ini_path, tmp_path, hasher, mocker):
        mocker.patch.object(data_folder, "prepare_data_root", side_effect=data_folder.DataFolderError("nope"))
        assert submit.apply_answers(answers(tmp_path), ctx, "h") == (400, {"data_folder.path": ["nope"]})
        assert not ini_path.exists()

    def test_write_failure_is_500(self, saving, ctx, tmp_path, hasher, mocker):
        mocker.patch.object(submit, "write_settings", side_effect=PermissionError(13, "denied"))
        status, body = submit.apply_answers(answers(tmp_path), ctx, "h")
        assert status == 500
        assert body["non_field_errors"][0].startswith("Could not write the settings file:")


class TestFinishInfo:

    @pytest.mark.parametrize("server_host, request_host, url", [
        ("0.0.0.0", "setuphost", "https://setuphost:9443/"),
        ("0.0.0.0", "[::1]", "https://[::1]:9443/"),
        ("192.168.1.5", "setuphost", "https://192.168.1.5:9443/"),
        ("fe80::5", "setuphost", "https://[fe80::5]:9443/"),
    ])
    def test_url(self, tmp_path, server_host, request_host, url):
        info = submit.finish_info(_plan(tmp_path, ADMIN, server_host=server_host), request_host, 9443)
        assert info["url"] == url and info["port_changed"] is False

    def test_kept_admins_and_warnings(self, tmp_path):
        info = submit.finish_info(_plan(tmp_path, None, warnings=["w"]), "h", 8080)
        assert (info["first_admin"], info["warnings"], info["port_changed"]) == (None, ["w"], True)
