# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``GET /api/setup/form/``: defaults, earlier values, admin accounts, and never a secret."""
import pytest

from sethlans_manager.setup_mode import db_probe, form
from tests.unit.manager.setup_mode._helpers import LAN_ADDRESS

FORM = "/api/setup/form/"
DB_SECRET = "stored-db-secret-pw"
HASH = "pbkdf2_sha256$1000000$storedsalt$storedhashvalue="
WIZARD_TOKEN = "old-wizard-token-value"
SESSION_ID = "old-wizard-session-value"

EARLIER = f"""[setup]
mode = manager
token = {WIZARD_TOKEN}
session_id = {SESSION_ID}

[server]
host = {LAN_ADDRESS}
port = 9443

[paths]
data_dir = {{data}}

[database]
engine = django.db.backends.postgresql
name = sethlans
host = db.example
port = 5433
user = seth
password = {DB_SECRET}
tls = true
tls_ca_file = /etc/ssl/ca.pem
tls_mode = verify-ca

[first_admin]
username = boss
email = boss@example.com
password_hash = {HASH}
replace_password = false
"""


@pytest.fixture
def accounts(mocker):
    return mocker.patch.object(form.db_probe, "check_database", return_value=db_probe.AdminAccounts(False, None))


@pytest.fixture
def free(mocker):
    return mocker.patch.object(form.data_folder, "free_bytes", return_value=500 * 1000 ** 3)


@pytest.fixture
def earlier(ini_path, tmp_path):
    ini_path.parent.mkdir(parents=True)
    ini_path.write_text(EARLIER.format(data=tmp_path / "data"))
    return ini_path


def _get(client, session):
    response = client.get(FORM, HTTP_X_SETUP_SESSION=session)
    assert response.status_code == 200
    return response


class TestNoSecrets:

    def test_no_stored_secret_in_the_body(self, setup_client, session, earlier, accounts, free):
        raw = _get(setup_client, session).content.decode("utf-8")
        for secret in (DB_SECRET, HASH, "storedhashvalue", WIZARD_TOKEN, SESSION_ID, session):
            assert secret not in raw

    def test_only_password_set_and_empty_password_fields(self, setup_client, session, earlier, accounts, free):
        body = _get(setup_client, session).json()
        assert body["database"]["password"] == ""
        assert body["database"]["password_set"] is True
        assert body["admin"]["password"] == ""
        assert "password_hash" not in body["admin"]

    def test_password_set_false_without_a_stored_one(self, setup_client, session, accounts, free):
        assert _get(setup_client, session).json()["database"]["password_set"] is False


class TestDefaults:

    def test_new_machine(self, setup_client, session, settings_dir, accounts, free):
        body = _get(setup_client, session).json()
        assert body["mode"] == "manager_worker"
        assert body["modes"] == ["manager", "manager_worker", "worker"]
        assert (body["hostname"], body["addresses"]) == ("setuphost", [LAN_ADDRESS])
        assert body["bind_addresses"] == ["0.0.0.0", "127.0.0.1", LAN_ADDRESS]
        assert body["server"] == {"host": "0.0.0.0", "port": 8080}
        assert body["data_folder"] == {"path": str(settings_dir), "free_bytes": 500 * 1000 ** 3, "low_space": False}
        assert body["database"]["engine"] == "sqlite" and body["database"]["tls"] is False
        assert body["database"]["tls_mode"] == "verify-full"
        assert body["admin"] == {"accounts_exist": False, "keep_existing": False, "username": "", "email": "",
                                 "password": "", "replace_password": False}
        assert (body["cards"], body["managers"], body["errors"], body["last_failure"]) == ([], [], [], None)

    @pytest.mark.parametrize("free_value, low", [(19 * 1000 ** 3, True), (20 * 1000 ** 3, False), (None, False)])
    def test_low_space_flag(self, setup_client, session, accounts, free, free_value, low):
        free.return_value = free_value
        assert _get(setup_client, session).json()["data_folder"]["low_space"] is low


class TestEarlierValues:

    def test_non_secret_values_are_pre_filled(self, setup_client, session, earlier, accounts, free, tmp_path):
        body = _get(setup_client, session).json()
        assert body["mode"] == "manager"
        assert body["server"] == {"host": LAN_ADDRESS, "port": 9443}
        assert body["data_folder"]["path"] == str(tmp_path / "data")
        assert {key: body["database"][key] for key in ("engine", "name", "host", "port", "user", "tls",
                                                       "tls_ca_file", "tls_mode")} == {
            "engine": "postgresql", "name": "sethlans", "host": "db.example", "port": 5433, "user": "seth",
            "tls": True, "tls_ca_file": "/etc/ssl/ca.pem", "tls_mode": "verify-ca"}
        assert (body["admin"]["username"], body["admin"]["email"]) == ("boss", "boss@example.com")

    def test_accounts_found_tick_keep_existing(self, setup_client, session, earlier, accounts, free):
        accounts.return_value = db_probe.AdminAccounts(True, None)
        admin = _get(setup_client, session).json()["admin"]
        assert (admin["accounts_exist"], admin["keep_existing"]) == (True, True)
        cfg = accounts.call_args.args[0]
        assert cfg["PASSWORD"] == DB_SECRET  # the probe gets it; the form does not
        assert accounts.call_args.kwargs == {"write_test": False}


SHAPE_MESSAGES = ("Must be an object.", "The form must be a JSON object.", "The data folder must be sent as")


def _shape_errors(errors):
    return {key: messages for key, messages in errors.items()
            if any(message.startswith(SHAPE_MESSAGES) for message in messages)}


class TestFormRoundTrip:

    def test_form_sent_back_unchanged_has_no_shape_errors(self, setup_client, session, accounts, free, check):
        body = _get(setup_client, session).json()
        errors, _plan = check(body)
        assert _shape_errors(errors) == {}
        # Only what the person must still choose or type is reported.
        assert set(errors) <= {"mode", "admin.username", "admin.password"}

    def test_form_filled_in_and_sent_back_validates(self, setup_client, session, accounts, free, check, tmp_path):
        body = _get(setup_client, session).json()
        body["mode"] = "manager"
        body["data_folder"]["path"] = str(tmp_path / "data")
        body["admin"].update(username="boss", password="Tr1cky-Setup-Pass!")
        errors, plan = check(body)
        assert errors == {}
        assert str(plan.data_root) == str(tmp_path / "data")
        assert (plan.server_host, plan.server_port) == ("0.0.0.0", 8080)
        assert plan.database == {"engine": "sqlite"}

    def test_earlier_form_sent_back_keeps_the_stored_db_password(self, setup_client, session, earlier, accounts,
                                                                 free, check, ctx, probe):
        from shared.settings_file import read_settings

        body = _get(setup_client, session).json()
        body["database"].update(host="127.0.0.1", tls=False)
        body["admin"]["password"] = "Tr1cky-Setup-Pass!"
        ctx.stored = read_settings(earlier)
        errors, plan = check(body)
        assert errors == {}
        assert plan.database["password"] == DB_SECRET
        assert probe.call_args.args[0]["PASSWORD"] == DB_SECRET


class TestFormErrors:

    def test_probe_failure_goes_into_errors(self, setup_client, session, earlier, accounts, free):
        accounts.side_effect = db_probe.ProbeError("host_unreachable")
        body = _get(setup_client, session).json()
        assert body["errors"] == ["Could not read the configured database: Could not reach the database server."]
        assert body["admin"]["accounts_exist"] is False

    def test_unusable_stored_settings_go_into_errors(self, setup_client, session, ini_path, accounts, free):
        ini_path.parent.mkdir(parents=True)
        ini_path.write_text("[database]\nengine = mysql\ntls = true\n")
        body = _get(setup_client, session).json()
        assert body["errors"] == ["The stored database settings are not usable: "
                                  "[database] tls = true needs tls_ca_file for MySQL and MariaDB"]

    def test_unreadable_file_still_returns_the_form(self, setup_client, session, ini_path, accounts, free):
        ini_path.parent.mkdir(parents=True)
        ini_path.write_text("garbage without a section")
        body = _get(setup_client, session).json()
        assert body["errors"][0].startswith("The settings file could not be read")
        assert body["mode"] == "manager_worker"

    def test_last_failure_and_leftovers_are_shown(self, setup_client, session, settings_dir, accounts, free):
        (settings_dir / "setup").mkdir(parents=True)
        (settings_dir / "setup" / "last_failure").write_text("Port 9443 was taken.")
        (settings_dir / "topology.json").write_text("{}")
        body = _get(setup_client, session).json()
        assert body["last_failure"] == "Port 9443 was taken."
        assert body["leftovers"] == [str(settings_dir / "topology.json")]
