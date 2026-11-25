# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Remove the run-time database overrides from a test's environment.

A CI job that runs the suite against MySQL or MariaDB sets
``SETHLANS_DB_ENGINE``/``NAME``/``HOST``/``PORT``/``USER``/``PASSWORD``
for the whole job. Tests of ``db_config.build_database_config`` that
expect the settings file or the SQLite default to decide must clear them
first; :func:`clear_db_env` removes every one ``db_config`` reads.
"""
import os

DB_ENV_PREFIX = "SETHLANS_DB_"
KNOWN_DB_ENV = (
    "SETHLANS_DATABASE_URL",
    "SETHLANS_DB_ENGINE", "SETHLANS_DB_NAME", "SETHLANS_DB_HOST", "SETHLANS_DB_PORT",
    "SETHLANS_DB_USER", "SETHLANS_DB_PASSWORD", "SETHLANS_DB_PASSWORD_FILE",
    "SETHLANS_DB_TLS", "SETHLANS_DB_TLS_CA_FILE", "SETHLANS_DB_TLS_MODE",
    "SETHLANS_MANAGER_DB_CONN_MAX_AGE",
)


def clear_db_env(monkeypatch):
    """Delete ``SETHLANS_DATABASE_URL``, every ``SETHLANS_DB_*`` and the connection age override."""
    present = [key for key in os.environ if key.upper().startswith(DB_ENV_PREFIX)]
    for key in set(KNOWN_DB_ENV) | set(present):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch
