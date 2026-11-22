# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: a project's ``unreal_measurement.keys`` (Unreal
per-machine memory test FR-MT-U-1, chunk 8; ``latest`` is gone).

Covers: QUEUED and RENDERING render jobs' keys first, newest submission
first, then keys with a current record under the current CVars, newest
record first, each key once; DONE jobs' keys only through a record; keys
of other CVars or without a current record are not listed, while an
active job's key stays listed after a CVar change with the new test, not
the old result;
at most 20 keys; the names come from the key's newest record, else from
its job; each key's machines are the job detail's rows; null in the
project list and for a worker-token request (whose project endpoint is
refused anyway).
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from workers.models import Job, Project, UnrealMeasurement, Worker
from workers.renderers.unreal_project_measurement import project_summary

from .machine_rows_helpers import PROJECTS, key_args, keyed_job, machine, machines, worker_context
from .machine_test_helpers import pair_record
from .unreal_helpers import unreal_asset
from .unreal_pilot_helpers import settings

pytestmark = pytest.mark.django_db


@pytest.fixture
def asset(db):
    machine('host-a')
    return unreal_asset()


def _job(asset, name, status='QUEUED', minutes=0):
    """A render job of its own key (sequence ``/Game/<name>.<name>``), submitted ``minutes`` from now."""
    job = keyed_job(asset, name=name, render_settings=settings(sequence=f'/Game/{name}.{name}'))
    Job.objects.filter(pk=job.pk).update(status=status, submitted_at=timezone.now() + timedelta(minutes=minutes))
    return job


def _record(job, minutes=0, host='host-a', **fields):
    """A ``fits`` record of the job's key for ``host``, created ``minutes`` from now, named like the job."""
    record = pair_record(*key_args(job), Worker.objects.get(hostname=host), verdict='fits')
    UnrealMeasurement.objects.filter(pk=record.pk).update(
        created_at=timezone.now() + timedelta(minutes=minutes), sequence=job.render_settings['unreal.sequence'],
        **fields)
    return record


def _keys(client, project_id):
    resp = client.get(f'{PROJECTS}{project_id}/')
    assert resp.status_code == 200, resp.data
    return resp.data['unreal_measurement']['keys']


def _sequences(keys):
    return [key['sequence'].split('.')[-1] for key in keys]


class TestOrder:

    def test_active_keys_newest_first_then_record_keys_newest_first(self, admin_client, asset):
        older, newer = _job(asset, 'Older', minutes=1), _job(asset, 'Newer', minutes=2)
        _job(asset, 'Busy', status='RENDERING')
        done_old, done_new = _job(asset, 'DoneOld', status='DONE'), _job(asset, 'DoneNew', status='DONE')
        _record(done_old, minutes=-5)
        _record(done_new, minutes=-1)
        _record(older, minutes=3)
        assert _sequences(_keys(admin_client, asset.project_id)) == ['Newer', 'Older', 'Busy', 'DoneNew', 'DoneOld']
        assert newer.unreal_target_sha256 in [key['target_sha256'] for key in _keys(admin_client, asset.project_id)]

    def test_done_job_without_a_record_is_not_listed(self, admin_client, asset):
        _job(asset, 'Done', status='DONE')
        _job(asset, 'Error', status='ERROR')
        assert _keys(admin_client, asset.project_id) == []

    def test_at_most_twenty_keys(self, admin_client, asset):
        for n in range(22):
            _job(asset, f'Key{n:02d}', minutes=n)
        keys = _keys(admin_client, asset.project_id)
        assert len(keys) == 20 and _sequences(keys)[0] == 'Key21' and _sequences(keys)[-1] == 'Key02'


class TestCVars:

    def test_record_keys_of_other_cvars_or_not_current_are_not_listed(self, admin_client, asset):
        stale, old = _job(asset, 'Stale', status='DONE'), _job(asset, 'Old', status='DONE')
        _record(stale, current=False)
        _record(old, overrides_sha256='f' * 64)
        assert _keys(admin_client, asset.project_id) == []

    def test_active_key_after_a_cvar_change_shows_the_new_test_not_the_old_result(self, admin_client, asset):
        job = _job(asset, 'Active')
        _record(job)
        assert [row['state'] for row in _keys(admin_client, asset.project_id)[0]['machines']] == ['fits']
        resp = admin_client.patch(f'{PROJECTS}{asset.project_id}/',
                                  {'unreal_cvar_overrides': {'r.Streaming.PoolSize': '3000'}}, format='json')
        assert resp.status_code == 200, resp.data
        [key] = _keys(admin_client, asset.project_id)
        # The old CVars' fits result is not used; the change started a test under the new CVars.
        assert [row['state'] for row in key['machines']] == ['testing']


class TestEntries:

    def test_names_from_the_newest_record_else_from_the_job(self, admin_client, asset):
        recorded, bare = _job(asset, 'Recorded', minutes=1), _job(asset, 'Bare')
        machine('host-b')
        _record(recorded, minutes=-2)
        _record(recorded, minutes=-1, host='host-b', map='/Game/Maps/Rec.Rec')
        first, second = _keys(admin_client, asset.project_id)
        assert (first['target_sha256'], first['map']) == (recorded.unreal_target_sha256, '/Game/Maps/Rec.Rec')
        assert (second['asset_id'], second['target_sha256'], second['engine_build'], second['map'],
                second['sequence'], second['preset']) == (
            asset.pk, bare.unreal_target_sha256, bare.engine_build, bare.render_settings['unreal.map'],
            '/Game/Bare.Bare', bare.render_settings['unreal.preset'])

    def test_each_keys_machines_match_the_job_detail(self, admin_client, asset):
        job = _job(asset, 'Match')
        machine('host-b')
        _record(job)
        [key] = _keys(admin_client, asset.project_id)
        assert key['machines'] == machines(admin_client, job)
        assert [row['hostname'] for row in key['machines']] == ['host-a', 'host-b']


class TestWhoSeesThem:

    def test_project_list_is_null(self, admin_client, asset):
        _job(asset, 'Listed')
        [row] = admin_client.get(PROJECTS).data
        assert row['unreal_measurement']['keys'] is None

    def test_worker_token_gets_null_keys(self, asset, make_worker_client):
        job = _job(asset, 'Hidden')
        worker, client, _token = make_worker_client('host-token')
        assert client.get(f'{PROJECTS}{asset.project_id}/').status_code == 403
        project = Project.objects.get(pk=asset.project_id)
        summary = project_summary(project, worker_context(worker.user))
        assert (summary['held_none'] + summary['held_pending'] + summary['held_failed'], summary['keys']) == (1, None)
        # The same call for an admin request lists the job's key.
        [key] = project_summary(project, {'view': None})['keys']
        assert key['target_sha256'] == job.unreal_target_sha256
