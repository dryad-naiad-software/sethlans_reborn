# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Real PostgreSQL, MySQL and MariaDB servers for the probe tests.

Environment (CI service containers set these; a case skips when its
engine's ``HOST`` is unset):

* ``SETHLANS_TEST_{POSTGRES,MYSQL,MARIADB}_HOST``, ``_PORT`` (default
  5432 / 3306), ``_USER``, ``_PASSWORD``: an account that may create and
  drop databases;
* ``SETHLANS_TEST_{POSTGRES,MYSQL,MARIADB}_TLS_CA``: the CA file that
  signed the server's certificate (for the host name in ``_HOST``); the
  TLS cases skip when it is unset.

Each test works in its own scratch database (``sethlans_it_<hex>``),
dropped afterwards. The admin tables are made with plain SQL in the
columns Django creates.
"""
import os
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

import pytest

KINDS = ("postgres", "mysql", "mariadb")
DEFAULT_PORTS = {"postgres": 5432, "mysql": 3306, "mariadb": 3306}
FORM_ENGINES = {"postgres": "postgresql", "mysql": "mysql", "mariadb": "mysql"}

AUTH_USER = {
    "postgres": (
        "CREATE TABLE auth_user (id serial PRIMARY KEY, password varchar(128) NOT NULL, "
        "last_login timestamptz NULL, is_superuser boolean NOT NULL, username varchar(150) NOT NULL UNIQUE, "
        "first_name varchar(150) NOT NULL DEFAULT '', last_name varchar(150) NOT NULL DEFAULT '', "
        "email varchar(254) NOT NULL DEFAULT '', is_staff boolean NOT NULL, is_active boolean NOT NULL, "
        "date_joined timestamptz NOT NULL DEFAULT now())"
    ),
    "mysql": (
        "CREATE TABLE auth_user (id integer AUTO_INCREMENT PRIMARY KEY, password varchar(128) NOT NULL, "
        "last_login datetime(6) NULL, is_superuser bool NOT NULL, username varchar(150) NOT NULL UNIQUE, "
        "first_name varchar(150) NOT NULL DEFAULT '', last_name varchar(150) NOT NULL DEFAULT '', "
        "email varchar(254) NOT NULL DEFAULT '', is_staff bool NOT NULL, is_active bool NOT NULL, "
        "date_joined datetime(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6))"
    ),
}
MANAGER_SETTINGS = "CREATE TABLE workers_managersettings (id integer PRIMARY KEY)"
INSERT_USER = ("INSERT INTO auth_user (password, is_superuser, username, is_staff, is_active) "
               "VALUES ('!', %s, %s, %s, TRUE)")


@dataclass
class Server:
    kind: str
    host: str
    port: int
    user: str
    password: str
    ca: Optional[str]

    @property
    def family(self) -> str:
        return "postgres" if self.kind == "postgres" else "mysql"

    def answers(self, name: str, **extra) -> dict:
        """The Advanced database section a person would submit for this server."""
        return {"engine": FORM_ENGINES[self.kind], "name": name, "host": self.host, "port": self.port,
                "user": self.user, "password": self.password, "tls": False, **extra}


def server_or_skip(kind: str) -> Server:
    prefix = f"SETHLANS_TEST_{kind.upper()}_"
    host = os.environ.get(prefix + "HOST", "").strip()
    if not host:
        pytest.skip(f"{prefix}HOST is not set (no {kind} server)")
    port = os.environ.get(prefix + "PORT", "").strip()
    return Server(kind, host, int(port) if port else DEFAULT_PORTS[kind],
                  os.environ.get(prefix + "USER", ""), os.environ.get(prefix + "PASSWORD", ""),
                  os.environ.get(prefix + "TLS_CA", "").strip() or None)


def ca_or_skip(server: Server) -> str:
    if not server.ca:
        pytest.skip(f"SETHLANS_TEST_{server.kind.upper()}_TLS_CA is not set (no TLS for {server.kind})")
    return server.ca


def _connect(server: Server, database: Optional[str] = None):
    if server.family == "postgres":
        import psycopg

        return psycopg.connect(host=server.host, port=server.port, user=server.user, password=server.password,
                               dbname=database or "postgres", autocommit=True, connect_timeout=10)
    import pymysql

    return pymysql.connect(host=server.host, port=server.port, user=server.user, password=server.password,
                           database=database, autocommit=True, connect_timeout=10)


def _run(server: Server, statements, database: Optional[str] = None):
    conn = _connect(server, database)
    try:
        with conn.cursor() as cursor:
            for sql, params in statements:
                cursor.execute(sql, params)
    finally:
        conn.close()


@contextmanager
def scratch_database(server: Server, users=(), tables=True):
    """A new database (optionally with the admin tables and ``users``); dropped after."""
    name = f"sethlans_it_{secrets.token_hex(6)}"
    _run(server, [(f"CREATE DATABASE {name}", None)])
    try:
        statements = []
        if tables:
            statements += [(AUTH_USER[server.family], None), (MANAGER_SETTINGS, None)]
        statements += [(INSERT_USER, (superuser, username, superuser)) for username, superuser in users]
        if statements:
            _run(server, statements, name)
        yield name
    finally:
        drop = f"DROP DATABASE {name} WITH (FORCE)" if server.family == "postgres" else f"DROP DATABASE {name}"
        _run(server, [(drop, None)])


def table_names(server: Server, database: str):
    conn = _connect(server, database)
    try:
        with conn.cursor() as cursor:
            if server.family == "postgres":
                cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            else:
                cursor.execute("SHOW TABLES")
            return sorted(row[0] for row in cursor.fetchall())
    finally:
        conn.close()


def connection_is_encrypted(server: Server, connection) -> bool:
    """Whether ``connection`` (a Django connection, open) runs over TLS."""
    with connection.cursor() as cursor:
        if server.family == "postgres":
            cursor.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")
            return bool(cursor.fetchone()[0])
        cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
        row = cursor.fetchone()
        return bool(row and row[1])
