# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Setup wizard database configuration endpoint (FR-A4, FR-DB1--FR-DB5).

For SQLite: applies migrations directly and returns ``{"status": "ok"}``.
For external databases: writes config to ``manager.ini`` and returns
``{"status": "restart_required"}``.  The launcher detects the config
change and restarts the manager process.
"""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.response import Response

from shared.frozen_paths import get_data_dir, is_frozen
from workers.authentication import SetupPhaseAuthentication
from workers.permissions import IsSetupPhaseUser
from workers.services.setup_lock import (
    setup_conflict_response,
    setup_mutation_lock,
)
from workers.services.setup_session import enforce_setup_session_binding
from workers.services.sentinel import append_checkpoint
from workers.services.setup import (
    apply_migrations,
    validate_db_connection,
    write_manager_ini,
)
from workers.utils.errors import setup_error

logger = logging.getLogger(__name__)

_ENGINE_MAP = {
    "sqlite": "django.db.backends.sqlite3",
    "postgresql": "django.db.backends.postgresql",
    "mysql": "django.db.backends.mysql",
}

_DEFAULT_PORTS = {
    "postgresql": "5432",
    "mysql": "3306",
}


def _get_data_dir() -> Path:
    if is_frozen():
        return get_data_dir("manager")
    return settings.BASE_DIR


def _get_ini_path() -> Path:
    return _get_data_dir() / "manager.ini"


@api_view(["POST"])
@authentication_classes([SetupPhaseAuthentication])
@permission_classes([IsSetupPhaseUser])
def setup_database_view(request):
    """POST /api/setup/database/ (FR-A4)."""
    enforce_setup_session_binding(request)
    with setup_mutation_lock() as acquired:
        if not acquired:
            return setup_conflict_response()
        engine = request.data.get("engine", "sqlite")
        if engine == "sqlite":
            return _handle_sqlite()
        return _handle_external(engine, request.data)


def _handle_sqlite():
    """SQLite: apply migrations directly, no restart needed."""
    try:
        apply_migrations()
    except Exception as exc:
        logger.exception("Migration failure on SQLite")
        return setup_error(
            "internal_error",
            f"Migration failed: {exc}",
            500,
        )

    append_checkpoint(_get_data_dir(), "database_configured")
    return Response({"status": "ok"})


def _handle_external(engine: str, data: dict):
    """External DB: validate, write config, signal restart."""
    if engine == "custom":
        engine_path = data.get("engine_path", "")
        if not engine_path:
            return setup_error(
                "invalid_input",
                "engine_path is required for custom engine",
                400,
            )
    else:
        engine_path = _ENGINE_MAP.get(engine)
        if not engine_path:
            return setup_error(
                "invalid_input",
                f"Unknown engine: {engine}",
                400,
            )

    host = data.get("host", "")
    port = data.get("port", _DEFAULT_PORTS.get(engine, ""))
    name = data.get("name", "")
    user = data.get("user", "")
    password = data.get("password", "")

    if not name:
        return setup_error(
            "invalid_input", "Database name is required", 400,
        )

    # Validate connection (FR-DB3)
    try:
        validate_db_connection(
            engine=engine_path,
            name=name,
            host=host,
            port=str(port),
            user=user,
            password=password,
        )
    except ConnectionError as exc:
        return setup_error(
            "invalid_input", str(exc), 400,
        )

    # Write to manager.ini [database] section
    config_updates = {
        "database.engine": engine if engine != "custom" else engine_path,
        "database.name": name,
        "database.host": host,
        "database.port": str(port),
        "database.user": user,
        "database.password": password,
    }
    write_manager_ini(config_updates, _get_ini_path())
    append_checkpoint(_get_data_dir(), "database_configured")

    return Response({"status": "restart_required"})
