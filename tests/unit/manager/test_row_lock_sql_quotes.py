# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The lock-SQL test oracle (``tests/row_lock_sql.py``) reads both name
quotings: ANSI ``"name"`` (PostgreSQL, SQLite) and MySQL/MariaDB
backticks. With backticks unhandled, the outer-join check never matched
and returned "no problem" for SQL PostgreSQL would reject.
"""
import pytest

from tests.row_lock_sql import (
    ansi_quoted, captured_sql, locked_tables, outer_joined_tables, postgres_lock_error, runnable,
)

MYSQL_JOIN = (
    "SELECT `workers_job`.`id` FROM `workers_job` "
    "LEFT OUTER JOIN `workers_worker` ON (`workers_job`.`assigned_worker_id` = `workers_worker`.`id`) "
    "WHERE `workers_job`.`id` = 7 "
)
ANSI_JOIN = ansi_quoted(MYSQL_JOIN)


@pytest.mark.parametrize("sql", [MYSQL_JOIN, ANSI_JOIN], ids=["backticks", "ansi"])
class TestRowLockSqlQuotes:
    def test_outer_joined_tables_are_unquoted(self, sql):
        assert outer_joined_tables(sql) == {"workers_worker"}

    def test_lock_of_names_are_unquoted(self, sql):
        quote = "`" if "`" in sql else '"'
        lock = f"FOR UPDATE OF {quote}workers_job{quote}, {quote}workers_asset{quote}"
        assert locked_tables(sql + lock) == {"workers_job", "workers_asset"}

    def test_unrestricted_lock_over_an_outer_join_is_an_error(self, sql):
        error = postgres_lock_error(sql + "FOR UPDATE")
        assert error == "FOR UPDATE without OF locks outer-joined ['workers_worker']"

    def test_lock_of_the_outer_joined_table_is_an_error(self, sql):
        quote = "`" if "`" in sql else '"'
        error = postgres_lock_error(sql + f"/* FOR UPDATE OF {quote}workers_worker{quote} SKIP LOCKED */")
        assert error == "FOR UPDATE OF locks outer-joined ['workers_worker']"

    def test_lock_of_the_inner_table_only_is_accepted(self, sql):
        quote = "`" if "`" in sql else '"'
        assert postgres_lock_error(sql + f"FOR UPDATE OF {quote}workers_job{quote}") is None


def test_outer_join_with_an_alias_reports_the_alias():
    sql = ("SELECT `T1`.`id` FROM `workers_job` LEFT OUTER JOIN `workers_worker` T3 "
           "ON (`workers_job`.`assigned_worker_id` = T3.`id`) FOR UPDATE OF `workers_job`")
    assert outer_joined_tables(sql) == {"T3"}
    assert postgres_lock_error(sql) is None


def test_captured_sql_turns_backticks_into_ansi_quotes():
    class Ctx:
        captured_queries = [{"sql": MYSQL_JOIN, "time": "0.001"}, {"sql": 'SELECT 1 FROM "t"', "time": "0"}]

    assert captured_sql(Ctx()) == [ANSI_JOIN, 'SELECT 1 FROM "t"']
    assert 'FROM "workers_job"' in ANSI_JOIN
    assert "`" not in ANSI_JOIN


def test_runnable_gives_back_what_the_database_ran():
    """MySQL reads ``"x"`` as a string: a captured statement runs again in its own quoting."""
    class Ctx:
        captured_queries = [{"sql": MYSQL_JOIN, "time": "0.001"}]

    [sql] = captured_sql(Ctx())
    assert sql == ANSI_JOIN
    assert runnable(sql) == MYSQL_JOIN
    assert runnable(ANSI_JOIN) == ANSI_JOIN  # plain text is left as it is
    assert runnable(dict([("t", sql)])["t"]) == MYSQL_JOIN  # kept through containers
