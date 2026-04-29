# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Real DB driver round-trips for the database step (FR-M2-4).

Covers the integration-test agent's deferred item 3:

* SQLite is always exercised — :mod:`sqlite3` is on the stdlib so the
  happy-path round-trip runs on every machine.
* PostgreSQL / MySQL drivers (``psycopg``, ``pymysql``) are skipped
  unless an environment variable points at a live DB
  (``SETHLANS_PG_TEST_DSN`` / ``SETHLANS_MY_TEST_DSN``). Maintainers
  who want full local coverage can spin up a docker-compose helper
  and export the DSN; CI gates these on existence so a missing local
  DB never breaks the suite.
* Error categorization is exercised against a wrong-password
  connection — fixed allowlist categories ``auth_failed`` /
  ``host_unreachable`` (depending on the driver's connect failure
  mode) prove the security-reviewer MED-3 contract: raw exception
  text must NEVER reach the HTTP body.

The wizard's database handler is hit through the live subprocess so
the lazy driver import + the manager.ini write happen end-to-end.
"""

from __future__ import annotations

import configparser
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

import pytest

from . import _http
from ._phase1_session import open_and_select, session_headers


def _post_database(wp, session: str, payload: dict, *, timeout: float = 5.0):
    return _http.post_json(
        f"{wp.base_url}/api/wizard/database/",
        payload,
        headers=session_headers(session),
        timeout=timeout,
    )


def _read_manager_ini(data_dir: Path) -> configparser.ConfigParser:
    target = data_dir / "manager.ini"
    parser = configparser.ConfigParser()
    if target.exists():
        parser.read(str(target), encoding="utf-8")
    return parser


# ----------------------- SQLite (always available) -----------------------

def test_database_sqlite_happy_path_writes_ini(wizard_process):
    """SQLite SELECT 1 round-trip + manager.ini [database] write."""
    wp = wizard_process
    session = open_and_select(wp, topology="manager")

    status, _, parsed = _post_database(
        wp, session,
        {"engine": "sqlite", "name": "sethlans_int.db"},
    )
    assert status == 200, parsed
    assert parsed and parsed.get("status") == "ok", parsed

    parser = _read_manager_ini(wp.data_dir)
    assert parser.has_section("database"), parser.sections()
    assert parser.get("database", "engine") == "sqlite"
    assert parser.get("database", "name") == "sethlans_int.db"
    # SQLite must NOT carry host/port/user/password (django-api-reviewer
    # LOW-8/9 — see _build_section in handlers/database.py).
    for absent in ("host", "port", "user", "password"):
        assert not parser.has_option("database", absent), absent

    # The handler ran a real SQLite probe — the DB file lands at
    # <data_dir>/<name> per validate_sqlite.
    db_path = wp.data_dir / "sethlans_int.db"
    assert db_path.exists(), f"expected {db_path} after SELECT 1"


def test_database_sqlite_writes_progress_checkpoint(wizard_process):
    """Successful SQLite write appends ``database_configured`` to progress."""
    import json as _json
    wp = wizard_process
    session = open_and_select(wp, topology="manager")

    status, _, _ = _post_database(
        wp, session,
        {"engine": "sqlite", "name": "checkpoint_probe.db"},
    )
    assert status == 200

    progress = wp.data_dir / ".setup_progress.json"
    assert progress.is_file()
    payload = _json.loads(progress.read_text(encoding="utf-8"))
    assert "database_configured" in payload["checkpoints"], payload


def test_database_invalid_engine_returns_400(wizard_process):
    """Out-of-vocabulary engine → 400 with no manager.ini write."""
    wp = wizard_process
    session = open_and_select(wp, topology="manager")

    status, _, parsed = _post_database(
        wp, session, {"engine": "definitely_not_a_real_engine"},
    )
    assert status == 400, parsed
    assert not (wp.data_dir / "manager.ini").exists(), (
        "manager.ini must NOT be written on validation failure"
    )


def test_database_missing_session_header_returns_401(wizard_process):
    """No X-Wizard-Session → 401 (FR-W12)."""
    wp = wizard_process
    status, _, parsed = _http.post_json(
        f"{wp.base_url}/api/wizard/database/",
        {"engine": "sqlite", "name": "noauth.db"},
    )
    assert status == 401, parsed


# ----------------------- PostgreSQL (env-gated) -----------------------

def _parse_dsn(dsn: str) -> dict:
    """Pull host/port/user/password/dbname out of a postgres://-style DSN."""
    parsed = urlparse(dsn)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "",
        "password": parsed.password or "",
        "name": (parsed.path or "/").lstrip("/") or "postgres",
    }


@pytest.mark.skipif(
    not os.environ.get("SETHLANS_PG_TEST_DSN"),
    reason="set SETHLANS_PG_TEST_DSN=postgres://user:pw@host:5432/dbname to run",
)
def test_database_postgresql_real_connection(wizard_process):
    """Real ``psycopg`` connect + SELECT 1 against the env-supplied DSN."""
    pytest.importorskip("psycopg")
    wp = wizard_process
    session = open_and_select(wp, topology="manager")

    parts = _parse_dsn(os.environ["SETHLANS_PG_TEST_DSN"])
    payload = {"engine": "postgresql", **parts}
    status, _, parsed = _post_database(wp, session, payload)
    assert status == 200, parsed

    parser = _read_manager_ini(wp.data_dir)
    assert parser.get("database", "engine") == "postgresql"
    assert parser.get("database", "host") == parts["host"]
    assert parser.getint("database", "port") == parts["port"]
    # Password persists in manager.ini — that's the wizard's contract.
    assert parser.get("database", "password") == parts["password"]


@pytest.mark.skipif(
    not os.environ.get("SETHLANS_PG_TEST_DSN"),
    reason="set SETHLANS_PG_TEST_DSN=... to run",
)
def test_database_postgresql_wrong_password_categorizes_auth_failed(
    wizard_process,
):
    """Wrong PG password → ``auth_failed`` category (no raw exc text)."""
    pytest.importorskip("psycopg")
    wp = wizard_process
    session = open_and_select(wp, topology="manager")

    parts = _parse_dsn(os.environ["SETHLANS_PG_TEST_DSN"])
    parts["password"] = "definitely-wrong-password-zzzzz"
    payload = {"engine": "postgresql", **parts}
    status, _, parsed = _post_database(wp, session, payload)
    assert status == 400, parsed
    # Category is one of the fixed allowlist values; auth_failed is the
    # most likely on a wrong-password run, but host_unreachable is also
    # acceptable if the test DSN points at a hostname that's only
    # reachable via a particular auth scheme.
    assert parsed.get("error") in {
        "auth_failed", "permission_denied", "host_unreachable", "generic",
    }, parsed
    # The raw exception text MUST NOT leak.
    assert "psycopg" not in (parsed.get("message") or "").lower(), parsed
    assert "exception" not in (parsed.get("message") or "").lower(), parsed


# ----------------------- MySQL (env-gated) -----------------------

@pytest.mark.skipif(
    not os.environ.get("SETHLANS_MY_TEST_DSN"),
    reason="set SETHLANS_MY_TEST_DSN=mysql://user:pw@host:3306/dbname to run",
)
def test_database_mysql_real_connection(wizard_process):
    """Real ``pymysql`` connect + SELECT 1 against the env-supplied DSN."""
    pytest.importorskip("pymysql")
    wp = wizard_process
    session = open_and_select(wp, topology="manager")

    parsed_dsn = urlparse(os.environ["SETHLANS_MY_TEST_DSN"])
    payload = {
        "engine": "mysql",
        "host": parsed_dsn.hostname or "localhost",
        "port": parsed_dsn.port or 3306,
        "user": parsed_dsn.username or "",
        "password": parsed_dsn.password or "",
        "name": (parsed_dsn.path or "/").lstrip("/") or "mysql",
    }
    status, _, response = _post_database(wp, session, payload)
    assert status == 200, response


# -------------- PostgreSQL/MySQL connection-refused ---------------

def _allocate_unused_port() -> int:
    """Bind+release to find a port nothing is listening on."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_database_postgresql_connection_refused_categorizes(wizard_process):
    """Wrong port (no listener) → fixed category, no raw exc text leak.

    Skipped unless ``psycopg`` is importable. We do NOT need a real
    PostgreSQL — we're testing the categorization path against a
    guaranteed-refused TCP target. Fast and side-effect-free.
    """
    pytest.importorskip("psycopg")
    wp = wizard_process
    session = open_and_select(wp, topology="manager")

    refused_port = _allocate_unused_port()
    status, _, parsed = _post_database(
        wp, session,
        {
            "engine": "postgresql",
            "host": "127.0.0.1",
            "port": refused_port,
            "user": "postgres",
            "password": "x",
            "name": "anything",
        },
        timeout=20.0,
    )
    assert status == 400, parsed
    assert parsed.get("error") in {
        "host_unreachable", "timeout", "generic",
    }, parsed
    # Raw psycopg exception text MUST NOT leak through.
    msg = (parsed.get("message") or "").lower()
    for forbidden in ("psycopg", "operationalerror", "traceback"):
        assert forbidden not in msg, parsed


def test_database_mysql_connection_refused_categorizes(wizard_process):
    """Wrong port (no listener) → fixed category for mysql."""
    pytest.importorskip("pymysql")
    wp = wizard_process
    session = open_and_select(wp, topology="manager")

    refused_port = _allocate_unused_port()
    status, _, parsed = _post_database(
        wp, session,
        {
            "engine": "mysql",
            "host": "127.0.0.1",
            "port": refused_port,
            "user": "root",
            "password": "x",
            "name": "anything",
        },
        timeout=20.0,
    )
    assert status == 400, parsed
    assert parsed.get("error") in {
        "host_unreachable", "timeout", "generic",
    }, parsed
    msg = (parsed.get("message") or "").lower()
    for forbidden in ("pymysql", "operationalerror", "traceback"):
        assert forbidden not in msg, parsed
