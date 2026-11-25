# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the project and asset delete lock tests (issues #287,
#288; ``services.delete_locks.lock_for_delete``).  Not a test module.

SQLite never emits ``FOR UPDATE``.  :func:`row_locks` tells the connection
it supports row locks and writes the lock clause as a trailing SQL
comment, so the captured SQL shows what PostgreSQL (``FOR UPDATE OF``) or
MariaDB (plain ``FOR UPDATE``) would receive and still runs here.
:func:`delete_captured` sends a DELETE under those locks, optionally
rolling it back so :func:`locked_pks` can read the locked rows again.
:func:`targets` gives the expected locked pks of a test scene's two
deletes.
"""

import contextlib
import re
from unittest.mock import patch

from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

from tests.row_lock_sql import captured_sql
from workers.models import Job, Project, SequenceDiscovery, SequenceSubmission

from .unreal_helpers import BUILD, RENDER_SETTINGS

D15 = ['workers_project', 'workers_job', 'workers_worker', 'workers_unrealmeasurement',
       'workers_sequencediscovery', 'workers_sequencesubmission']
NO_WORKER_LOCK = [table for table in D15 if table != 'workers_worker']
FROM_TABLE = re.compile(r'\bFROM "(workers_\w+)"')
WRITE = re.compile(r'^\s*(UPDATE|DELETE|INSERT)\b')
PROJECT_WRITE_LOCK = 'UPDATE "workers_project"'


class Undo(Exception):
    """Rolls the delete's savepoint back so the locked rows can be read again."""


@contextlib.contextmanager
def row_locks(of=True):
    """Emulate PostgreSQL (``of=True``) or MariaDB (no ``FOR UPDATE OF``); the clause is a trailing comment."""
    real = connection.ops.for_update_sql

    def commented(**kwargs):
        return f'/* {real(**kwargs)} */'

    with patch.object(connection.features, 'has_select_for_update', True), \
            patch.object(connection.features, 'has_select_for_update_of', of), \
            patch.object(connection.ops, 'for_update_sql', commented):
        yield


def delete_captured(client, url, of=True, undo=False):
    """DELETE ``url`` under emulated row locks; ``(status, [sql])``, rolled back when ``undo``."""
    with row_locks(of), CaptureQueriesContext(connection) as ctx:
        try:
            with transaction.atomic():
                status = client.delete(url).status_code
                if undo:
                    raise Undo
        except Undo:
            pass
    return status, captured_sql(ctx)


def lock_statements(sqls):
    """``[(index, table, sql)]`` of the locking statements, in order."""
    return [(i, FROM_TABLE.search(sql).group(1), sql) for i, sql in enumerate(sqls) if 'FOR UPDATE' in sql]


def collector_writes(sqls):
    """Indexes of the writes other than the project's zero-row write lock."""
    return [i for i, sql in enumerate(sqls) if WRITE.match(sql) and not sql.startswith(PROJECT_WRITE_LOCK)]


def locked_pks(sql):
    """Run a captured lock again (its lock clause is a comment here); the pks it selects."""
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return [row[0] for row in cursor.fetchall()]


def sequence_rows(asset, versions):
    """One DONE discovery and one submission per version for ``asset``; ``[discovery], [submission]``."""
    key = dict(project=asset.project, asset=asset, engine_build=BUILD, map=RENDER_SETTINGS['unreal.map'],
               sequence=RENDER_SETTINGS['unreal.sequence'], preset=RENDER_SETTINGS['unreal.preset'])
    discoveries, submissions = [], []
    for version in versions:
        discovery = SequenceDiscovery.objects.create(**key, target_sha256=f'{version:064x}', status='DONE')
        discoveries.append(discovery)
        submissions.append(SequenceSubmission.objects.create(
            **key, discovery=discovery, version=version, name=f'v{version}', piece_seconds=30, lead_in_ms=0,
            status='QUEUED'))
    return discoveries, submissions


def pks_of(*rows):
    """The rows' pks, ascending."""
    return sorted(row.pk for row in rows)


def targets(scene):
    """``{target: (url, {locked table: expected pks})}`` of the scene's two deletes (Project row left out)."""
    w = scene.workers
    project_jobs = Job.objects.filter(asset__project=scene.project)
    (d1, d2), (s1, s2) = scene.first_seq
    (d3,), (s3,) = scene.second_seq
    first_jobs = sorted(project_jobs.filter(asset=scene.first).values_list('pk', flat=True))
    return {
        'project': (f'/api/projects/{scene.project.pk}/', {
            'workers_job': sorted(project_jobs.values_list('pk', flat=True)),
            'workers_worker': pks_of(w['first'], w['second'], w['third']),
            'workers_unrealmeasurement': pks_of(scene.measured, scene.derived, scene.other),
            'workers_sequencediscovery': pks_of(d1, d2, d3),
            'workers_sequencesubmission': pks_of(s1, s2, s3),
        }),
        'asset': (f'/api/assets/{scene.first.pk}/', {
            'workers_job': first_jobs,
            'workers_worker': pks_of(w['first'], w['second']),
            'workers_unrealmeasurement': pks_of(scene.measured, scene.derived),
            'workers_sequencediscovery': pks_of(d1, d2),
            'workers_sequencesubmission': pks_of(s1, s2),
        }),
    }


def project_vanishes(monkeypatch, viewset):
    """Make the project of the fetched object disappear right after ``get_object`` (a concurrent delete)."""
    real = viewset.get_object

    def fetch_then_gone(view):
        instance = real(view)
        project_pk = instance.pk if isinstance(instance, Project) else instance.project_id
        Project.objects.filter(pk=project_pk).delete()
        return instance

    monkeypatch.setattr(viewset, 'get_object', fetch_then_gone)
