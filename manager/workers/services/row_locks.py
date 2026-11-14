# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Row-locking helper that is safe across every supported database backend.

PostgreSQL rejects ``SELECT ... FOR UPDATE`` when the query contains a
LEFT OUTER JOIN ("FOR UPDATE cannot be applied to the nullable side of an
outer join"). ``select_related()`` across a nullable foreign key produces
exactly such a join, so ``select_for_update().select_related(...)`` on a
nullable relation fails there, while SQLite silently ignores ``FOR UPDATE``
and never shows the problem (issue #216).

:func:`lock_own_rows` locks only the base model's rows:

* Backends that support ``FOR UPDATE OF`` (PostgreSQL, MySQL 8, Oracle)
  get ``FOR UPDATE OF <base table>``, and the requested relations are
  still joined in the same query.
* Backends that row-lock but cannot restrict the lock (MariaDB) skip the
  joins, so only the base row is locked. Relations are fetched with
  ``prefetch_related`` (separate, non-locking queries), so a multi-row lock
  does not turn into N+1 queries.
* Backends with no row locking at all (SQLite) ignore the clause; the
  joins are kept so the query count stays the same.
"""

from django.db import connections, router

# ``of`` argument meaning "the queryset's own model only".
SELF_ONLY = ('self',)


def lock_own_rows(queryset, *related):
    """Return ``queryset`` locked ``FOR UPDATE`` on its own table only.

    Args:
        queryset: A model manager or queryset (e.g. ``Job.objects``).
        *related: ``select_related`` lookups to load with the locked row.

    Returns:
        A queryset that, when evaluated inside ``transaction.atomic()``,
        locks only the rows of ``queryset.model``.
    """
    locked = queryset.select_for_update()
    # ``locked.db`` honours an explicit ``.using(alias)`` and the router's
    # write database (``select_for_update`` marks the queryset for write).
    alias = locked.db
    if not isinstance(alias, str):
        alias = router.db_for_write(queryset.model)
    features = connections[alias].features
    if features.has_select_for_update and not features.has_select_for_update_of:
        # Cannot name the locked table: drop the joins so the lock cannot
        # reach related rows; prefetch the relations without locking them.
        return locked.prefetch_related(*related) if related else locked
    locked = queryset.select_for_update(of=SELF_ONLY)
    if related:
        locked = locked.select_related(*related)
    return locked
