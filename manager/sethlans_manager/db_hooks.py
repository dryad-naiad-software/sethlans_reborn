# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
``connection_created`` signal hooks applied by ``workers.apps.ready``.

Split out from ``db_config.py`` to keep that module focused on
``DATABASES`` dict construction (pure function) and to hold the 300-line
file limit.

SQLite PRAGMAs applied per new connection (Phase 4 Waitress migration):

- ``journal_mode=WAL``    — writers do not block readers; writers queue
  on a single writer lock instead of taking the whole DB.
- ``busy_timeout=30000``  — matches ``OPTIONS.timeout`` (30 s) so the
  lock-wait window is identical at both the Python-driver and
  SQLite-engine layers (reconciled in ``db_config``).
- ``synchronous=NORMAL``  — fsync on WAL checkpoint only, not every
  commit.  Safe with WAL + power-loss-tolerant disks; matches Django's
  recommendation for threaded servers.

No-op when ``connection.vendor != 'sqlite'`` so external DBs (Postgres /
MySQL) that reach the same signal do NOT receive SQLite-only PRAGMAs.
"""

import logging

from .db_config import _SQLITE_BUSY_TIMEOUT_MS

logger = logging.getLogger(__name__)


def _apply_sqlite_pragmas(sender, connection, **kwargs):
    """Apply WAL / busy_timeout / synchronous PRAGMAs on SQLite connect."""
    del sender
    del kwargs
    if getattr(connection, "vendor", None) != "sqlite":
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute(
                f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS};"
            )
            cursor.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        # PRAGMAs are best-effort: a read-only tempdb or an in-memory
        # test fixture may reject WAL.  Log and continue rather than
        # crash the request/process.
        logger.warning(
            "db_hooks: failed to apply SQLite PRAGMAs",
            exc_info=True,
        )


def register_connection_hooks() -> None:
    """Attach ``_apply_sqlite_pragmas`` to Django's ``connection_created``.

    Called from ``workers.apps.WorkersConfig.ready``.  Idempotent —
    Django's signal framework de-duplicates repeated connects when
    ``dispatch_uid`` is stable.
    """
    from django.db.backends.signals import connection_created
    connection_created.connect(
        _apply_sqlite_pragmas,
        dispatch_uid="sethlans_manager.db_hooks.sqlite_pragmas",
    )
