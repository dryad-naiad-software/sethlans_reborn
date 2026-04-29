# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Per-engine DB connection validators (FR-M2-4).

Extracted from ``handlers/database.py`` to keep that module under the
project's 300-line ceiling. Behaviour is unchanged — the database
handler imports :func:`live_connect` here and uses the same
fixed-allowlist error categories.

Driver imports are LAZY so the wizard test suite can run without
``psycopg`` / ``pymysql`` installed; a missing driver translates to
the ``generic`` category at request time.

Categories returned (security-reviewer MED-3): ``auth_failed``,
``host_unreachable``, ``db_not_found``, ``permission_denied``,
``ssl_error``, ``timeout``, ``generic``. The handler maps each to a
fixed user-facing message — raw driver exceptions are NEVER returned
in the HTTP body.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

CONNECTION_TIMEOUT_SECONDS = 10


def _redact(text: str, password: str) -> str:
    if password:
        return text.replace(password, "<redacted>")
    return text


def categorize_exception(exc: BaseException, password: str) -> str:
    """Map a driver exception to one of the fixed category strings."""
    name = exc.__class__.__name__.lower()
    text = str(exc).lower()
    if password:
        text = text.replace(password.lower(), "<redacted>")
    if "auth" in name or "auth" in text or "password" in text:
        return "auth_failed"
    if "ssl" in name or "ssl" in text or "tls" in text:
        return "ssl_error"
    if "permission" in text or "denied" in text or "privilege" in text:
        return "permission_denied"
    if "timeout" in name or "timeout" in text or "timed out" in text:
        return "timeout"
    if "host" in text or "could not connect" in text or "refused" in text:
        return "host_unreachable"
    if "database" in text and (
        "not exist" in text or "unknown" in text or "not found" in text
    ):
        return "db_not_found"
    return "generic"


def validate_sqlite(
    name: str, data_dir: Path,
) -> Tuple[bool, Optional[str]]:
    """SQLite validation: data dir writable + ``SELECT 1`` round-trip."""
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".sethlans_db_probe"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        logger.info("sqlite data dir not writable: %s", exc)
        return False, "permission_denied"
    db_path = data_dir / (name or "sethlans.db")
    try:
        conn = sqlite3.connect(
            str(db_path),
            timeout=CONNECTION_TIMEOUT_SECONDS,
        )
        try:
            conn.execute("SELECT 1")
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.info("sqlite probe failed: %s", exc)
        return False, "generic"
    return True, None


def validate_postgresql(
    name: str, host: str, port: int, user: str, password: str,
) -> Tuple[bool, Optional[str]]:
    """PostgreSQL validation via ``psycopg`` (lazy import)."""
    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError:
        logger.error("psycopg not installed; cannot validate PostgreSQL")
        return False, "generic"
    try:
        conn = psycopg.connect(
            dbname=name,
            host=host,
            port=int(port),
            user=user,
            password=password,
            connect_timeout=CONNECTION_TIMEOUT_SECONDS,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — driver exceptions vary
        category = categorize_exception(exc, password)
        logger.info(
            "postgresql probe failed (%s): %s",
            category, _redact(str(exc), password),
        )
        return False, category
    return True, None


def validate_mysql(
    name: str, host: str, port: int, user: str, password: str,
) -> Tuple[bool, Optional[str]]:
    """MySQL/MariaDB validation via ``pymysql`` (lazy import)."""
    try:
        import pymysql  # type: ignore[import-not-found]
    except ImportError:
        logger.error("pymysql not installed; cannot validate MySQL")
        return False, "generic"
    try:
        conn = pymysql.connect(
            host=host,
            port=int(port),
            user=user,
            password=password,
            database=name,
            connect_timeout=CONNECTION_TIMEOUT_SECONDS,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — driver exceptions vary
        category = categorize_exception(exc, password)
        logger.info(
            "mysql probe failed (%s): %s",
            category, _redact(str(exc), password),
        )
        return False, category
    return True, None


def live_connect(
    engine: str, payload: dict, data_dir: Path,
) -> Tuple[bool, Optional[str]]:
    """Dispatch to the right validator. Returns ``(ok, category)``."""
    name = (payload.get("name") or "").strip()
    host = (payload.get("host") or "").strip()
    port = payload.get("port") or 0
    user = (payload.get("user") or "").strip()
    password = payload.get("password") or ""
    if engine == "sqlite":
        return validate_sqlite(name, data_dir)
    if engine == "postgresql":
        return validate_postgresql(name, host, port, user, password)
    if engine == "mysql":
        return validate_mysql(name, host, port, user, password)
    if engine == "custom":
        # Custom backend — wizard does NOT live-connect. The user is
        # responsible for verifying their backend.
        return True, None
    return False, "generic"


__all__ = [
    "CONNECTION_TIMEOUT_SECONDS",
    "live_connect",
    "categorize_exception",
    "validate_sqlite",
    "validate_postgresql",
    "validate_mysql",
]
