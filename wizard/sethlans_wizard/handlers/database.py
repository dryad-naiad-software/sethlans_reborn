# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""``POST /api/wizard/database/`` — Database step (FR-M2-4).

Real driver connect to the operator-supplied DB, run ``SELECT 1``,
close. Driver exceptions translate to a fixed allowlist of error
categories — raw exception text NEVER reaches the HTTP body
(security-reviewer MED-3). The validator implementations live in
``wizard.sethlans_wizard.db_validate`` to keep this module under the
project's 300-line ceiling.

INI write contract (FR-M2-4 / django-api-reviewer LOW-8/9):
* The wizard writes only the SHORT engine name (``sqlite``,
  ``postgresql``, ``mysql``, or the user-supplied ``engine_path`` for
  ``custom``) plus name, host, port, user, password.
* No OPTIONS-level keys; the manager's ``db_config._build_external``
  injects ``connect_timeout`` at runtime.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Iterable

from wizard.sethlans_wizard import db_validate, progress
from wizard.sethlans_wizard.checkpoints import DATABASE_CONFIGURED
from wizard.sethlans_wizard.handlers import _wsgi
from wizard.sethlans_wizard.handlers.auth import session_header_valid
from wizard.sethlans_wizard.manager_ini import update_manager_ini

logger = logging.getLogger(__name__)

VALID_ENGINES = frozenset({"sqlite", "postgresql", "mysql", "custom"})

# Fixed per-category user-facing messages. Raw exception text NEVER
# leaks to the user (security-reviewer MED-3).
_CATEGORY_MESSAGES: dict[str, str] = {
    "auth_failed": "database authentication failed",
    "host_unreachable": "database host is not reachable",
    "db_not_found": "database name was not found on the server",
    "permission_denied": "database user lacks permission",
    "ssl_error": "database SSL handshake failed",
    "timeout": "database connection timed out",
    "generic": "could not connect to database",
}


def _live_connect(engine: str, payload: dict, data_dir: Path):
    """Backwards-compatible alias used by ``handlers/verify.py``.

    The verify handler dispatches DB checks through this thin shim so
    the database-handler API remains stable when implementation moves
    between modules.
    """
    return db_validate.live_connect(engine, payload, data_dir)


def make_database_handler(data_dir: Path) -> Callable:
    """Return a WSGI handler bound to *data_dir* for FR-M2-4."""
    if not isinstance(data_dir, Path):
        data_dir = Path(data_dir)

    def handler(environ: dict, start_response: Callable) -> Iterable[bytes]:
        return _handle(environ, start_response, data_dir)

    return handler


def _read_request(environ: dict):
    """Run boilerplate guards. Returns ``(payload, None)`` or
    ``(None, (status, body))``."""
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method != "POST":
        return None, (405, {"error": "Method Not Allowed"})
    if _wsgi.query_string_has_forbidden_key(environ):
        return None, (400, {"error": "session token must not appear in URL"})
    if not session_header_valid(environ):
        return None, (401, {"error": "missing or invalid X-Wizard-Session header"})
    body = _wsgi.read_body(environ)
    if len(body) > _wsgi.BODY_MAX:
        return None, (400, {"error": "request body too large"})
    payload = _wsgi.parse_json_body(body)
    if payload is None:
        return None, (400, {"error": "request body must be JSON"})
    return payload, None


def _build_section(engine: str, payload: dict) -> dict[str, object]:
    """Return the dict to pass to ``update_manager_ini`` for *engine*."""
    section: dict[str, object] = {"engine": engine}
    if engine == "custom":
        ep = payload.get("engine_path")
        if isinstance(ep, str) and ep:
            section["engine_path"] = ep
    section["name"] = payload.get("name") or ""
    if engine != "sqlite":
        section["host"] = payload.get("host") or ""
        section["port"] = int(payload.get("port") or 0)
        section["user"] = payload.get("user") or ""
        section["password"] = payload.get("password") or ""
    return section


def _handle(
    environ: dict,
    start_response: Callable,
    data_dir: Path,
) -> Iterable[bytes]:
    payload, err = _read_request(environ)
    if err is not None:
        status, body = err
        extra = [("Allow", "POST")] if status == 405 else None
        return _wsgi.send_json(start_response, body, status=status, extra_headers=extra)

    engine = payload.get("engine")
    if not isinstance(engine, str) or engine not in VALID_ENGINES:
        return _wsgi.send_json(
            start_response,
            {"error": "engine must be one of "
             "sqlite | postgresql | mysql | custom"},
            status=400,
        )

    ok, category = db_validate.live_connect(engine, payload, data_dir)
    if not ok:
        cat = category or "generic"
        return _wsgi.send_json(
            start_response,
            {
                "error": cat,
                "message": _CATEGORY_MESSAGES.get(cat, _CATEGORY_MESSAGES["generic"]),
            },
            status=400,
        )

    section = _build_section(engine, payload)
    try:
        update_manager_ini(data_dir, "database", section)
    except OSError as exc:
        logger.error(
            "Could not write manager.ini under %s: %s", data_dir, exc,
        )
        return _wsgi.send_json(
            start_response,
            {"error": "could not write manager.ini"},
            status=500,
        )

    progress.append_checkpoint(data_dir, DATABASE_CONFIGURED)
    return _wsgi.send_json(
        start_response,
        {"status": "ok"},
        status=200,
    )


__all__ = ["make_database_handler"]
