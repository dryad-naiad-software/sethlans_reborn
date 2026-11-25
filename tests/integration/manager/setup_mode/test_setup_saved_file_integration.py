# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
What a real ``run_manager --setup`` leaves on disk after it saved.

``sethlans.ini`` holds the admin password as a ``pbkdf2_sha256`` hash and
never in plain text; the setup-mode log and the console output hold
neither the admin password nor a database password, also after a
refused submit that carried one. The file and the data folder are
owner-only.
"""
import configparser
import datetime

from django.contrib.auth.hashers import check_password

from shared.owner_only_acl import verify_owner_only

from .setup_process import ADMIN_PASSWORD, EXIT_SAVED, Client, free_port, manager_answers

DB_PASSWORD = "Plain-Db-Secret-3b8d2e61"
MESSAGES_UNREACHABLE = (
    "Could not reach the database server.",
    "No connection to the database within 5 seconds (is it running and reachable?).",
)


def _read_ini(path):
    parser = configparser.RawConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    return parser


def _texts(proc):
    """Every file the process wrote its text to: the settings file, its log, its console."""
    return {
        "sethlans.ini": proc.ini_path.read_bytes(),
        "setup-mode.log": proc.log_path.read_bytes(),
        "console": proc.output().encode("utf-8"),
    }


def _refused_postgres_submit(client, proc, data_root):
    """A submit carrying a database password to a closed port: refused on ``database``."""
    database = {"engine": "postgresql", "name": "sethlans", "host": "127.0.0.1", "port": free_port(),
                "user": "sethlans", "password": DB_PASSWORD, "tls": False}
    resp = client.submit(manager_answers(data_root, proc.setup_port, database=database))
    assert resp.status == 400, resp.raw
    assert resp.json()["database"][0] in MESSAGES_UNREACHABLE
    return resp


class TestSetupSavedFileIntegration:
    def test_settings_file_holds_a_hash_and_no_plain_secret(self, setup_process, tmp_path, data_root):
        proc = setup_process()
        client = Client(proc.waitress_port)
        assert client.auth(proc.token).status == 200
        refused = _refused_postgres_submit(client, proc, data_root)
        assert DB_PASSWORD.encode() not in refused.raw

        resp = client.submit(manager_answers(data_root, proc.setup_port))

        assert resp.status == 200, resp.raw
        assert proc.wait(timeout=10) == EXIT_SAVED
        for name, data in _texts(proc).items():
            assert ADMIN_PASSWORD.encode() not in data, f"plain admin password in {name}"
            assert DB_PASSWORD.encode() not in data, f"plain database password in {name}"
        stored = _read_ini(proc.ini_path)
        password_hash = stored.get("first_admin", "password_hash")
        assert password_hash.startswith("pbkdf2_sha256$1000000$")
        assert check_password(ADMIN_PASSWORD, password_hash)
        assert stored.get("first_admin", "username") == "farmadmin"
        assert stored.get("first_admin", "email") == "admin@example.com"
        assert stored.get("first_admin", "replace_password") == "false"
        # The refused submit's database never reached the file.
        assert dict(stored.items("database")) == {"engine": "sqlite"}

    def test_saved_sections_and_owner_only_files(self, setup_process, tmp_path, data_root):
        proc = setup_process()
        client = Client(proc.waitress_port)
        assert client.auth(proc.token).status == 200

        resp = client.submit(manager_answers(data_root, proc.setup_port))

        assert resp.status == 200, resp.raw
        assert proc.wait(timeout=10) == EXIT_SAVED
        stored = _read_ini(proc.ini_path)
        assert stored.get("setup", "version") == "1"
        assert stored.get("setup", "mode") == "manager"
        completed = datetime.datetime.fromisoformat(stored.get("setup", "completed_at"))
        assert completed.tzinfo is not None
        assert abs((datetime.datetime.now(datetime.timezone.utc) - completed).total_seconds()) < 120
        assert stored.get("server", "host") == "127.0.0.1"
        assert stored.get("server", "port") == str(proc.setup_port)
        assert stored.get("paths", "data_dir") == str(data_root)
        assert verify_owner_only(proc.ini_path)
        assert verify_owner_only(proc.settings_dir)
        assert verify_owner_only(proc.log_path.parent)
        assert verify_owner_only(proc.log_path)
        for folder in (data_root, data_root / "manager", data_root / "worker"):
            assert folder.is_dir() and verify_owner_only(folder), folder
        assert (data_root / ".sethlans-data").is_file()
        # SQLite is only ever read in setup mode: no database file was made.
        assert not (data_root / "manager" / "db.sqlite3").exists()

    def test_setup_token_is_never_logged(self, setup_process, tmp_path, data_root):
        proc = setup_process()
        client = Client(proc.waitress_port)
        assert client.auth("wrong-" + proc.token[:8]).status == 403
        assert client.auth(proc.token).status == 200
        resp = client.submit(manager_answers(data_root, proc.setup_port))
        assert resp.status == 200, resp.raw
        assert proc.wait(timeout=10) == EXIT_SAVED

        for name, data in _texts(proc).items():
            assert proc.token.encode() not in data, f"setup token in {name}"
            assert client.session.encode() not in data, f"session token in {name}"
