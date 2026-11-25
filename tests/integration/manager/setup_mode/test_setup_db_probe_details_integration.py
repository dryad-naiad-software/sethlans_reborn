# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Details of the probe's own connection on real servers (skip without
``SETHLANS_TEST_<ENGINE>_HOST``, ``db_servers``).

* PostgreSQL: the server-side ``statement_timeout`` is 5 s on the probe
  connection while it reads the accounts.
* MySQL and MariaDB: the probe table has a primary key, so the
  connection test passes with the server's primary-key requirement on
  (``sql_require_primary_key`` / ``innodb_force_primary_key``); a table
  without one is refused there, which shows the setting is in effect.
"""
import pytest

from sethlans_manager.setup_mode import db_probe

from .db_servers import primary_key_required, run_sql, scratch_database, server_or_skip, table_names
from .probe_pipeline import probe

PROBE_TABLE_PREFIX = "sethlans_setup_probe_"


class TestProbeConnectionDetailsIntegration:
    @pytest.mark.parametrize("write_test", [False, True], ids=["read-only", "with-connection-test"])
    def test_postgres_probe_runs_with_a_5_s_statement_timeout(self, django_db_blocker, tmp_path, monkeypatch,
                                                              write_test):
        server = server_or_skip("postgres")
        seen = []
        real_read = db_probe._read_admins

        def read_and_look(connection, username):
            with connection.cursor() as cursor:
                cursor.execute("SHOW statement_timeout")
                seen.append(cursor.fetchone()[0])
            return real_read(connection, username)

        monkeypatch.setattr(db_probe, "_read_admins", read_and_look)
        with scratch_database(server, users=[("farmadmin", True)]) as name:
            found = probe(django_db_blocker, server.answers(name), tmp_path / "data", "farmadmin", write_test)

        assert found.accounts_exist is True
        assert seen == ["5s"]

    @pytest.mark.parametrize("kind", ["mysql", "mariadb"])
    def test_probe_table_has_a_primary_key(self, django_db_blocker, tmp_path, kind):
        server = server_or_skip(kind)
        with scratch_database(server, users=[("farmadmin", True)]) as name:
            with primary_key_required(server) as setting:
                with pytest.raises(Exception) as refused:
                    run_sql(server, "CREATE TABLE no_key (id integer)", name)

                found = probe(django_db_blocker, server.answers(name), tmp_path / "data", "farmadmin")

            assert found.accounts_exist is True, setting
            assert "no_key" not in table_names(server, name), refused.value
            assert not [t for t in table_names(server, name) if t.startswith(PROBE_TABLE_PREFIX)]
