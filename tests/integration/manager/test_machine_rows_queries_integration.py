# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: what the machine list and the routing note's memory
part cost (Unreal per-machine memory test NF-3, FR-MT-U-1, FR-MT-U-2,
chunk 8).

Covers: an admin job list costs the same for 1 job as for 20 jobs over
five projects whose keys all have records, reads the records in one
statement and the workers in one, and adds at most one query over the
same list when no note needs a memory part; the job detail costs the same
for one machine as for six with records, and its note and machine list
share one read of the records; the project detail costs the same for one
key as for twenty (with and without records).
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from workers.models import Worker

from .fits_nowhere_helpers import dnf_record
from .machine_rows_helpers import PROJECTS, WAITING, key_args, keyed_job, machine
from .machine_test_helpers import pair_record
from .unreal_helpers import caps, unreal_asset, unreal_project
from .unreal_pilot_helpers import JOBS, settings

pytestmark = pytest.mark.django_db


def _count(client, url, params=None):
    with CaptureQueriesContext(connection) as ctx:
        resp = client.get(url, params or {})
    assert resp.status_code == 200, resp.data
    return [query['sql'] for query in ctx.captured_queries], resp


def _renders(resp):
    """The render rows of a job list (the records' pilot jobs are rows too)."""
    return [row for row in resp.data if row['unreal_kind'] == 'render']


def _reading(sql, table):
    """The statements whose own FROM is ``table``."""
    return [statement for statement in sql if f'FROM "{table}"' in statement.split(' WHERE ')[0]]


def _keyed_jobs(project_no, count, workers):
    """``count`` jobs of their own keys in a new project; each key has a record per worker in some state."""
    asset = unreal_asset(unreal_project(name=f'RowsQ{project_no}'), name=f'RowsQA{project_no}')
    jobs = []
    for n in range(count):
        job = keyed_job(asset, name=f'RowsQ{project_no}-{n}',
                        render_settings=settings(sequence=f'/Game/Q{project_no}x{n}.Q{project_no}x{n}'))
        for index, worker in enumerate(workers):
            if (n + index) % 3 == 0:
                pair_record(*key_args(job), worker, pilot='QUEUED')
            elif (n + index) % 3 == 1:
                dnf_record(*key_args(job), worker)
        jobs.append(job)
    return jobs


@pytest.fixture
def workers(db):
    return [machine('host-a'), machine('host-b')]


class TestJobList:

    def test_one_job_and_twenty_over_five_projects_cost_the_same(self, admin_client, workers):
        _keyed_jobs(0, 1, workers)
        one, resp = _count(admin_client, JOBS, {'renderer': 'UNREAL'})
        assert [row['routing_note'] for row in _renders(resp)] == [WAITING.format(n=1)]
        for project_no in range(1, 5):
            _keyed_jobs(project_no, 5 if project_no < 4 else 4, workers)
        twenty, resp = _count(admin_client, JOBS, {'renderer': 'UNREAL'})
        assert len(_renders(resp)) == 20 and all(row['routing_note'] for row in _renders(resp))
        assert len(twenty) == len(one)
        assert len(_reading(twenty, 'workers_unrealmeasurement')) == 1
        assert len(_reading(twenty, 'workers_worker')) == 1

    def test_the_memory_part_adds_at_most_one_query(self, admin_client, workers):
        for project_no in range(4):
            _keyed_jobs(project_no, 5, workers)
        with_memory, resp = _count(admin_client, JOBS, {'renderer': 'UNREAL'})
        assert all(row['routing_note'] for row in _renders(resp))
        Worker.objects.update(unreal_capabilities=caps(builds=('5.7.1-46543410',)))
        without, resp = _count(admin_client, JOBS, {'renderer': 'UNREAL'})
        assert {row['routing_note'][:31] for row in _renders(resp)} == {'No online worker reports Unreal'}
        assert len(with_memory) - len(without) <= 1


class TestJobDetail:

    def test_one_machine_and_six_cost_the_same(self, admin_client):
        job = keyed_job()
        first = machine('host-0')
        pair_record(*key_args(job), first, pilot='QUEUED')
        one, resp = _count(admin_client, f'{JOBS}{job.pk}/')
        assert len(resp.data['unreal_machines']) == 1
        for n in range(1, 6):
            worker = machine(f'host-{n}')
            if n % 2:
                dnf_record(*key_args(job), worker)
        six, resp = _count(admin_client, f'{JOBS}{job.pk}/')
        assert len(resp.data['unreal_machines']) == 6 and resp.data['routing_note'] == WAITING.format(n=3)
        assert len(six) == len(one)

    @pytest.mark.parametrize('status', ['QUEUED', 'RENDERING'])
    def test_note_and_machine_list_share_one_read(self, admin_client, workers, status):
        [job] = _keyed_jobs(0, 1, workers)
        job.status = status
        job.save(update_fields=['status'])
        sql, resp = _count(admin_client, f'{JOBS}{job.pk}/')
        assert len(resp.data['unreal_machines']) == 2
        assert len(_reading(sql, 'workers_unrealmeasurement')) == 1
        assert len(_reading(sql, 'workers_worker')) == 1


class TestProjectDetail:

    def test_one_key_and_twenty_cost_the_same(self, admin_client, workers):
        asset = unreal_asset()
        pair_record(*key_args(keyed_job(asset, name='RowsP0')), workers[0], verdict='fits')
        one, resp = _count(admin_client, f'{PROJECTS}{asset.project_id}/')
        assert len(resp.data['unreal_measurement']['keys']) == 1
        for n in range(1, 20):
            job = keyed_job(asset, name=f'RowsP{n}', render_settings=settings(sequence=f'/Game/P{n}.P{n}'))
            if n % 2:
                pair_record(*key_args(job), workers[n % 2], verdict='fits')
        twenty, resp = _count(admin_client, f'{PROJECTS}{asset.project_id}/')
        keys = resp.data['unreal_measurement']['keys']
        assert len(keys) == 20 and all(len(key['machines']) == 2 for key in keys)
        # The bare keys' names come from one extra read of their jobs (none when every key has a record).
        assert len(twenty) <= len(one) + 1
        assert len(_reading(twenty, 'workers_unrealmeasurement')) == len(_reading(one, 'workers_unrealmeasurement'))
