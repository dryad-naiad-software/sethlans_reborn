# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The read-only admin probe and the connection test on real SQLite files.

In process, through the submit's own database path (answers ->
``[database]`` -> ``DATABASES`` entry -> probe): a WAL database whose
superuser is still only in the ``-wal`` file, a cleanly closed one, a
missing file, missing tables, a database with no superuser, and the
default file under the planned data folder. The probe never changes a
byte. Then through a real setup process: an existing username needs
the replace box, and keeping the existing admins needs one to exist.
"""
import configparser

import pytest

from sethlans_manager.setup_mode.validate_admin import ACCOUNT_EXISTS, NO_ACCOUNT_TO_KEEP

from .probe_pipeline import digests, probe, sqlite_db
from .setup_process import ADMIN_PASSWORD, EXIT_SAVED, Client, fields_of, manager_answers


def _sqlite(path):
    return {"engine": "sqlite", "name": str(path)}


class TestSqliteProbeIntegration:
    def test_wal_database_with_open_writer_shows_its_superuser(self, django_db_blocker, tmp_path):
        path = tmp_path / "db" / "db.sqlite3"
        writer = sqlite_db(path, users=[("farmadmin", True)], wal=True, keep_open=True)
        try:
            assert path.with_name("db.sqlite3-wal").stat().st_size > 0  # rows not checkpointed
            before = digests(path)

            found = probe(django_db_blocker, _sqlite(path), tmp_path / "data", "farmadmin")
            other = probe(django_db_blocker, _sqlite(path), tmp_path / "data", "someone")

            assert (found.accounts_exist, found.username_exists) == (True, True)
            assert (other.accounts_exist, other.username_exists) == (True, False)
            assert digests(path) == before
        finally:
            writer.close()

    def test_closed_wal_database_shows_its_superuser(self, django_db_blocker, tmp_path):
        path = tmp_path / "db.sqlite3"
        sqlite_db(path, users=[("farmadmin", True)], wal=True)
        before = digests(path)

        found = probe(django_db_blocker, _sqlite(path), tmp_path / "data", "farmadmin")

        assert (found.accounts_exist, found.username_exists) == (True, True)
        assert digests(path) == before

    def test_missing_file_has_no_accounts_and_is_not_created(self, django_db_blocker, tmp_path):
        path = tmp_path / "absent" / "db.sqlite3"

        found = probe(django_db_blocker, _sqlite(path), tmp_path / "data", "farmadmin")

        assert (found.accounts_exist, found.username_exists) == (False, False)
        assert not path.exists()
        assert not path.parent.exists()

    @pytest.mark.parametrize("tables", [(), ("auth_user",), ("workers_managersettings",)],
                             ids=["no-tables", "auth-only", "manager-settings-only"])
    def test_missing_tables_mean_no_accounts(self, django_db_blocker, tmp_path, tables):
        path = tmp_path / "db.sqlite3"
        users = [("farmadmin", True)] if "auth_user" in tables else []
        sqlite_db(path, users=users, tables=tables)

        found = probe(django_db_blocker, _sqlite(path), tmp_path / "data", "farmadmin")

        assert (found.accounts_exist, found.username_exists) == (False, False)

    def test_staff_without_superuser_is_no_admin_to_keep(self, django_db_blocker, tmp_path):
        path = tmp_path / "db.sqlite3"
        sqlite_db(path, users=[("operator", False)])

        found = probe(django_db_blocker, _sqlite(path), tmp_path / "data", "operator")

        assert (found.accounts_exist, found.username_exists) == (False, True)

    def test_inactive_superuser_is_no_admin_to_keep(self, django_db_blocker, tmp_path):
        path = tmp_path / "db.sqlite3"
        sqlite_db(path, users=[("farmadmin", True, False)])

        found = probe(django_db_blocker, _sqlite(path), tmp_path / "data", "farmadmin")

        assert (found.accounts_exist, found.username_exists) == (False, True)

    def test_default_file_follows_the_planned_data_folder(self, django_db_blocker, tmp_path):
        data_root = tmp_path / "data"
        sqlite_db(data_root / "manager" / "db.sqlite3", users=[("farmadmin", True)])

        found = probe(django_db_blocker, {"engine": "sqlite"}, data_root, "farmadmin")
        elsewhere = probe(django_db_blocker, {"engine": "sqlite"}, tmp_path / "other", "farmadmin")

        assert found.accounts_exist is True
        assert elsewhere.accounts_exist is False

    def test_connection_test_never_writes_sqlite(self, django_db_blocker, tmp_path):
        path = tmp_path / "db" / "db.sqlite3"
        sqlite_db(path, users=[("farmadmin", True)])
        before = digests(path)

        probe(django_db_blocker, _sqlite(path), tmp_path / "data", "farmadmin", write_test=True)

        assert digests(path) == before
        assert sorted(p.name for p in path.parent.iterdir()) == ["db.sqlite3"]  # no journal, no -wal


class TestSqliteAdminChoicesThroughSetupIntegration:
    def test_existing_username_needs_replace_then_saves_replace_true(self, setup_process, tmp_path, data_root):
        path = tmp_path / "db" / "db.sqlite3"
        sqlite_db(path, users=[("farmadmin", True)], wal=True)
        before = digests(path)
        proc = setup_process()
        client = Client(proc.waitress_port)
        assert client.auth(proc.token).status == 200
        admin = {"username": "farmadmin", "password": ADMIN_PASSWORD}

        refused = client.submit(manager_answers(data_root, proc.setup_port, _sqlite(path), admin))
        saved = client.submit(manager_answers(data_root, proc.setup_port, _sqlite(path),
                                              {**admin, "replace_password": True}))

        assert refused.status == 400, refused.raw
        assert fields_of(refused) == {"admin.username": [ACCOUNT_EXISTS]}
        assert saved.status == 200, saved.raw
        assert proc.wait(timeout=10) == EXIT_SAVED
        stored = configparser.RawConfigParser(interpolation=None)
        stored.read(proc.ini_path, encoding="utf-8")
        assert stored.get("first_admin", "replace_password") == "true"
        assert stored.get("database", "name") == str(path)
        assert digests(path) == before

    def test_keep_existing_needs_an_active_superuser(self, setup_process, tmp_path, data_root):
        missing = tmp_path / "none" / "db.sqlite3"
        inactive = tmp_path / "inactive" / "db.sqlite3"
        present = tmp_path / "db" / "db.sqlite3"
        sqlite_db(inactive, users=[("farmadmin", True, False), ("operator", False)])
        sqlite_db(present, users=[("farmadmin", True)])
        proc = setup_process()
        client = Client(proc.waitress_port)
        assert client.auth(proc.token).status == 200
        keep = {"keep_existing": True}

        refused = client.submit(manager_answers(data_root, proc.setup_port, _sqlite(missing), keep))
        refused_inactive = client.submit(manager_answers(data_root, proc.setup_port, _sqlite(inactive), keep))
        saved = client.submit(manager_answers(data_root, proc.setup_port, _sqlite(present), keep))

        assert fields_of(refused) == {"admin.keep_existing": [NO_ACCOUNT_TO_KEEP]}
        assert fields_of(refused_inactive) == {"admin.keep_existing": [NO_ACCOUNT_TO_KEEP]}
        assert saved.status == 200, saved.raw
        assert saved.json()["first_admin"] is None
        assert proc.wait(timeout=10) == EXIT_SAVED
        stored = configparser.RawConfigParser(interpolation=None)
        stored.read(proc.ini_path, encoding="utf-8")
        assert not stored.has_section("first_admin")
        assert not missing.exists()
