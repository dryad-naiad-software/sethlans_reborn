# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The connection test and the read-only admin probe on real PostgreSQL,
MySQL and MariaDB servers, without TLS (the TLS cases are in
``test_setup_db_tls_integration``). Each case skips when its server's
``SETHLANS_TEST_<ENGINE>_HOST`` is unset (``db_servers``).

In process, through the submit's own database path; then one real
setup process per engine saves the server into ``sethlans.ini``.
"""
import configparser
import logging

import pytest

from sethlans_manager.setup_mode import db_probe

from .db_servers import KINDS, scratch_database, server_or_skip, table_names
from .probe_pipeline import probe
from .setup_process import ADMIN_PASSWORD, EXIT_SAVED, Client, manager_answers

PROBE_TABLE_PREFIX = "sethlans_setup_probe_"
# A shorter test password ("root") could match ordinary log text by chance.
MIN_DISTINCT_PASSWORD = 8


@pytest.fixture(params=KINDS)
def server(request):
    return server_or_skip(request.param)


@pytest.mark.django_db  # in-process probes run ahead of Playwright's loop (issue #277)
class TestDatabaseServerProbeIntegration:
    def test_connection_test_passes_and_leaves_no_probe_table(self, server, django_db_blocker, tmp_path):
        with scratch_database(server) as name:
            found = probe(django_db_blocker, server.answers(name), tmp_path / "data", "farmadmin")

            assert (found.accounts_exist, found.username_exists) == (False, False)
            tables = table_names(server, name)
        assert tables == ["auth_user", "workers_managersettings"]
        assert not [table for table in tables if table.startswith(PROBE_TABLE_PREFIX)]

    def test_connection_test_on_an_empty_database_is_no_accounts(self, server, django_db_blocker, tmp_path):
        with scratch_database(server, tables=False) as name:
            found = probe(django_db_blocker, server.answers(name), tmp_path / "data")

            assert found.accounts_exist is False
            assert table_names(server, name) == []

    def test_wrong_password_gives_the_auth_message(self, server, django_db_blocker, tmp_path, caplog):
        wrong = "Wrong-Db-Secret-5a1c9e77"
        with scratch_database(server) as name:
            with caplog.at_level(logging.DEBUG):
                with pytest.raises(db_probe.ProbeError) as raised:
                    probe(django_db_blocker, server.answers(name, password=wrong), tmp_path / "data")

        assert raised.value.category == "auth_failed"
        assert raised.value.message == "The database refused the user name or password."
        assert raised.value.__cause__ is None and raised.value.__suppress_context__
        assert wrong not in caplog.text

    def test_missing_database_gives_a_message(self, server, django_db_blocker, tmp_path):
        with pytest.raises(db_probe.ProbeError) as raised:
            probe(django_db_blocker, server.answers("sethlans_it_absent_db"), tmp_path / "data")

        assert raised.value.category == "db_not_found"
        assert raised.value.message == "The database does not exist."

    @pytest.mark.parametrize("write_test", [False, True], ids=["read-only", "with-connection-test"])
    def test_probe_sees_an_existing_superuser(self, server, django_db_blocker, tmp_path, write_test):
        users = [("farmadmin", True), ("operator", False)]
        with scratch_database(server, users=users) as name:
            answers = server.answers(name)
            admin = probe(django_db_blocker, answers, tmp_path / "data", "farmadmin", write_test)
            staff = probe(django_db_blocker, answers, tmp_path / "data", "operator", write_test)
            nobody = probe(django_db_blocker, answers, tmp_path / "data", "someone", write_test)

            assert (admin.accounts_exist, admin.username_exists) == (True, True)
            assert (staff.accounts_exist, staff.username_exists) == (True, True)
            assert (nobody.accounts_exist, nobody.username_exists) == (True, False)
            assert table_names(server, name) == ["auth_user", "workers_managersettings"]

    def test_staff_only_database_has_no_admin_to_keep(self, server, django_db_blocker, tmp_path):
        with scratch_database(server, users=[("operator", False)]) as name:
            found = probe(django_db_blocker, server.answers(name), tmp_path / "data", "operator", False)

        assert (found.accounts_exist, found.username_exists) == (False, True)


class TestDatabaseServerSubmitIntegration:
    def test_real_setup_saves_the_server_and_logs_no_secret(self, server, setup_process, tmp_path, data_root):
        with scratch_database(server, users=[("farmadmin", True)]) as name:
            proc = setup_process()
            client = Client(proc.waitress_port)
            assert client.auth(proc.token).status == 200
            admin = {"username": "farmadmin", "password": ADMIN_PASSWORD, "replace_password": True}

            resp = client.submit(manager_answers(data_root, proc.setup_port, server.answers(name), admin))

            assert resp.status == 200, resp.raw
            assert proc.wait(timeout=10) == EXIT_SAVED
            assert table_names(server, name) == ["auth_user", "workers_managersettings"]

        stored = configparser.RawConfigParser(interpolation=None)
        stored.read(proc.ini_path, encoding="utf-8")
        assert stored.get("database", "engine") == server.answers(name)["engine"]
        assert stored.get("database", "host") == server.host
        assert stored.get("database", "port") == str(server.port)
        assert stored.get("database", "name") == name
        assert stored.get("database", "tls") == "false"
        assert stored.get("first_admin", "replace_password") == "true"
        assert stored.get("first_admin", "password_hash").startswith("pbkdf2_sha256$")
        log = proc.log_path.read_bytes() + proc.output().encode("utf-8")
        assert ADMIN_PASSWORD.encode() not in proc.ini_path.read_bytes()
        assert ADMIN_PASSWORD.encode() not in log
        if len(server.password) >= MIN_DISTINCT_PASSWORD:
            # The database password is kept in the owner-only file, never in a log.
            assert stored.get("database", "password") == server.password
            assert server.password.encode() not in log
