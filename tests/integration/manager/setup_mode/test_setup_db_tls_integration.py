# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Database TLS for the connection test and the admin probe.

* Real servers (skip without ``SETHLANS_TEST_<ENGINE>_HOST`` and
  ``_TLS_CA``): with the right CA the test passes and the probe's own
  connection is encrypted; with a CA that did not sign the server's
  certificate it fails with the TLS message. PostgreSQL uses the default
  mode, ``verify-full``.
* A real setup process, no server needed: TLS on without a CA file is
  refused for MySQL/MariaDB and for PostgreSQL ``verify-full`` and
  ``verify-ca``, and setup stays open for the next submit.
"""
import pytest

from sethlans_manager.setup_mode import db_probe

from .db_servers import KINDS, ca_or_skip, connection_is_encrypted, scratch_database, server_or_skip
from .probe_pipeline import open_probe_connection, planned_entry, probe
from .setup_process import Client, manager_answers

CA_NEEDED = "A CA file is needed to check the database server's certificate."
TLS_FAILED = "The TLS connection to the database failed (check the CA file and the TLS mode)."


@pytest.fixture(params=KINDS)
def tls_server(request):
    server = server_or_skip(request.param)
    ca_or_skip(server)
    return server


@pytest.fixture(scope="module")
def unrelated_ca(tmp_path_factory):
    """A self-signed certificate that signed no database server's certificate."""
    from shared.cert_utils import generate_self_signed_cert

    folder = tmp_path_factory.mktemp("unrelated-ca")
    cert = folder / "tls" / "cert.pem"
    generate_self_signed_cert(cert, folder / "tls" / "key.pem")
    return str(cert)


class TestDatabaseTlsServerIntegration:
    def test_good_ca_passes_and_the_connection_is_encrypted(self, tls_server, django_db_blocker, tmp_path):
        with scratch_database(tls_server, users=[("farmadmin", True)]) as name:
            answers = tls_server.answers(name, tls=True, tls_ca_file=tls_server.ca)

            found = probe(django_db_blocker, answers, tmp_path / "data", "farmadmin")

            assert (found.accounts_exist, found.username_exists) == (True, True)
            entry = planned_entry(answers, tmp_path / "data")
            if tls_server.family == "postgres":
                assert entry["OPTIONS"]["sslmode"] == "verify-full"
                assert entry["OPTIONS"]["sslrootcert"] == tls_server.ca
            else:
                assert entry["OPTIONS"]["ssl"] == {"ca": tls_server.ca}
                assert "local_infile" not in entry["OPTIONS"]
            connection = open_probe_connection(entry)
            try:
                with django_db_blocker.unblock():
                    connection.ensure_connection()
                    assert connection_is_encrypted(tls_server, connection)
            finally:
                connection.close()

    def test_wrong_ca_fails_with_the_tls_message(self, tls_server, django_db_blocker, tmp_path, unrelated_ca):
        with scratch_database(tls_server) as name:
            answers = tls_server.answers(name, tls=True, tls_ca_file=unrelated_ca)

            with pytest.raises(db_probe.ProbeError) as raised:
                probe(django_db_blocker, answers, tmp_path / "data")

        assert raised.value.category == "ssl_error"
        assert raised.value.message == TLS_FAILED


class TestDatabaseTlsRefusedThroughSetupIntegration:
    @pytest.mark.parametrize("database", [
        {"engine": "mysql", "tls": True},
        {"engine": "postgresql", "tls": True},
        {"engine": "postgresql", "tls": True, "tls_mode": "verify-full"},
        {"engine": "postgresql", "tls": True, "tls_mode": "verify-ca"},
    ], ids=["mysql-mariadb", "postgresql-default-mode", "postgresql-verify-full", "postgresql-verify-ca"])
    def test_tls_on_without_a_ca_file_is_refused(self, setup_process, tmp_path, database, data_root):
        proc = setup_process()
        client = Client(proc.waitress_port)
        assert client.auth(proc.token).status == 200
        answers = {"name": "sethlans", "host": "127.0.0.1", "user": "sethlans", "password": "x" * 12,
                   **database}

        refused = client.submit(manager_answers(data_root, proc.setup_port, answers))
        again = client.submit(manager_answers(data_root, proc.setup_port, answers))

        assert refused.status == 400, refused.raw
        assert refused.json() == {"database.tls_ca_file": [CA_NEEDED]}
        assert again.status == 400  # back to IDLE after a refusal, not 409
        assert proc.proc.poll() is None
        assert not proc.ini_path.exists()
