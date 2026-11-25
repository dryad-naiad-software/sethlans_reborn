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

Names may be quoted ANSI-style (``"name"``: PostgreSQL, SQLite) or with
backticks (MySQL, MariaDB); the helpers here accept both. Tests that match
captured SQL text against ``"name"`` patterns read it through
:func:`captured_sql`, which turns backtick quotes into double quotes.
"""

import re

_LOCK_RE = re.compile(
    r'FOR (?:NO KEY )?UPDATE(?: OF (?P<of>.+?))?(?: NOWAIT| SKIP LOCKED)?\s*(?:\*/)?\s*$',
)
_QUOTES = '"`'
_OUTER_JOIN_RE = re.compile(
    r'LEFT OUTER JOIN (?P<table>["`]?\w+["`]?)(?: (?P<alias>["`]?\w+["`]?))? ON',
)


def _unquote(name):
    return name.strip().strip(_QUOTES)


def _names(raw):
    return {_unquote(name) for name in raw.split(',')}


def ansi_quoted(sql):
    """``sql`` with MySQL/MariaDB backtick name quotes turned into ``"``.

    Django quotes names only with backticks on MySQL; captured SQL carries
    parameters inlined, so test data must not contain backticks.
    """
    return sql.replace('`', '"')


class CapturedSql(str):
    """A captured statement for matching (names quoted ``"name"``); ``.raw`` is what the database ran.

    MySQL and MariaDB read ``"x"`` as a string, not a name, so a test that
    runs a captured statement again must run :func:`runnable` of it.
    """

    raw = ''

    def __new__(cls, raw):
        text = super().__new__(cls, ansi_quoted(raw))
        text.raw = raw
        return text


def runnable(sql):
    """The SQL text to execute again: the original of a :class:`CapturedSql`."""
    return sql.raw if isinstance(sql, CapturedSql) else sql


def captured_sql(ctx):
    """The statements of a ``CaptureQueriesContext`` as :class:`CapturedSql`."""
    return [CapturedSql(query['sql']) for query in ctx.captured_queries]


def outer_joined_tables(sql):
    """Return the table names/aliases on the nullable side of an outer join."""
    return {
        _unquote(m.group('alias') or m.group('table'))
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
