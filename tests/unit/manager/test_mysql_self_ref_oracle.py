# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unit tests for the MySQL error 1093 oracle in ``tests.mysql_self_ref_sql`` (issue #318).

Querysets are compiled with Django 5.2's real MySQL compiler (PyMySQL is
installed, no server is contacted). They pin which same-table subqueries
Django sends to MySQL as they are (flagged) and which it rewrites itself
(not flagged), so the oracle neither misses the bug nor cries wolf.
"""

import pytest
from django.db.models import Exists, OuterRef, Subquery

from tests.mysql_self_ref_sql import (
    delete_sql, mysql_compile_connection, mysql_write_checks, self_subquery_error, update_sql,
)
from workers.models import Job, SequenceSubmission


@pytest.fixture
def mysql():
    return mysql_compile_connection()


def _render_of_same_submission():
    return Job.objects.filter(sequence_submission=OuterRef('sequence_submission'), unreal_kind='render')


class TestConnection:

    def test_is_mysql_not_mariadb(self, mysql):
        assert mysql.vendor == 'mysql' and not mysql.mysql_is_mariadb
        assert mysql.features.update_can_self_select is False
        assert mysql.features.delete_can_self_reference_subquery is False


class TestFlagged:
    """Single-table statements Django sends unchanged: MySQL raises 1093."""

    def test_update_where_not_exists_same_table(self, mysql):
        sql = update_sql(Job.objects.filter(~Exists(_render_of_same_submission())), mysql, status='CANCELED')
        assert 'workers_job' in self_subquery_error(sql)

    def test_update_where_pk_in_same_model_queryset(self, mysql):
        inner = Job.objects.filter(unreal_kind='audio').values('pk')
        sql = update_sql(Job.objects.filter(pk__in=inner), mysql, status='CANCELED')
        assert self_subquery_error(sql) is not None

    def test_update_set_from_same_table(self, mysql):
        first = Job.objects.filter(asset=OuterRef('asset')).order_by('pk').values('name')[:1]
        sql = update_sql(Job.objects.all(), mysql, error_message=Subquery(first))
        assert self_subquery_error(sql) is not None


class TestNotFlagged:
    """Statements MySQL accepts, or that Django rewrites for MySQL."""

    def test_update_by_plain_key_list(self, mysql):
        sql = update_sql(Job.objects.filter(pk__in=[1, 2, 3], status='QUEUED'), mysql, status='CANCELED')
        assert self_subquery_error(sql) is None

    def test_update_of_another_table_reading_jobs(self, mysql):
        first = Job.objects.filter(sequence_submission=OuterRef('pk')).values('unreal_target_sha256')[:1]
        sql = update_sql(SequenceSubmission.objects.all(), mysql, piece_target_sha256=Subquery(first))
        assert self_subquery_error(sql) is None

    def test_delete_with_same_model_queryset_is_materialized(self, mysql):
        sql = delete_sql(Job.objects.filter(pk__in=Job.objects.filter(unreal_kind='audio').values('pk')), mysql)
        assert 'SELECT * FROM (' in sql
        assert self_subquery_error(sql) is None

    @pytest.mark.django_db
    def test_update_with_a_join_is_preselected(self):
        with mysql_write_checks() as errors:
            Job.objects.filter(asset__project__name='nothing').update(status='CANCELED')
        assert errors == []
