# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Test oracle for PostgreSQL's FOR UPDATE / outer join rule (issue #216).

PostgreSQL raises "FOR UPDATE cannot be applied to the nullable side of an
outer join" when a locking SELECT would lock a table that sits on the
nullable side of a LEFT OUTER JOIN. Plain ``FOR UPDATE`` locks every table
in the query; ``FOR UPDATE OF a, b`` locks only the named tables.

SQLite (the CI database) never emits ``FOR UPDATE``, so tests compile or
capture the locking SQL and run it through :func:`postgres_lock_error`.
"""

import re

_LOCK_RE = re.compile(
    r'FOR (?:NO KEY )?UPDATE(?: OF (?P<of>.+?))?(?: NOWAIT| SKIP LOCKED)?\s*(?:\*/)?\s*$',
)
_OUTER_JOIN_RE = re.compile(
    r'LEFT OUTER JOIN (?P<table>"?\w+"?)(?: (?P<alias>\w+))? ON',
)


def _names(raw):
    return {name.strip().strip('"') for name in raw.split(',')}


def outer_joined_tables(sql):
    """Return the table names/aliases on the nullable side of an outer join."""
    return {
        (m.group('alias') or m.group('table')).strip('"')
        for m in _OUTER_JOIN_RE.finditer(sql)
    }


def locked_tables(sql):
    """Return the ``FOR UPDATE OF`` names, ``None`` for an unrestricted lock.

    Raises:
        AssertionError: if ``sql`` is not a locking query.
    """
    match = _LOCK_RE.search(sql)
    assert match, f'Not a FOR UPDATE query: {sql}'
    return _names(match.group('of')) if match.group('of') else None


def postgres_lock_error(sql):
    """Return why PostgreSQL would reject this locking SQL, or ``None``."""
    nullable = outer_joined_tables(sql)
    if not nullable:
        return None
    locked = locked_tables(sql)
    if locked is None:
        return f'FOR UPDATE without OF locks outer-joined {sorted(nullable)}'
    clash = locked & nullable
    if clash:
        return f'FOR UPDATE OF locks outer-joined {sorted(clash)}'
    return None
