# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: admin writes on per-machine memory test jobs (Unreal
per-machine memory test FR-MT-R-2, FR-MT-R-5; sequence FR-SW-2).

Covers ``unreal_settings._drop_unchanged_settings``: an admin PATCH that
re-sends a pilot's stored ``render_settings`` (manager-only
``unreal.card_kind``, size, shot set, frames and draft included), in any
key order, passes and keeps the stored value and key; a value of another
JSON type (``1`` sent as ``1.0`` or ``true``, ``[1920, 1080]`` as
``[1920.0, 1080.0]``) is a change, so the manager-only keys are refused
(400) and nothing is written; a changed card kind meets the pilot rule.

Covers ``worker_job_writes._set_and_save``: an admin PATCH or PUT that
sets an Unreal render job QUEUED again from ERROR or CANCELED clears its
accepted ``unreal_memory_report``; a PATCH that leaves the status alone
keeps it.
"""

import copy

import pytest

from shared.unreal.validation import shot_set_sha256
from workers.models import Job, JobStatus
from workers.services.unreal_target import Target

from .machine_test_helpers import locked_create, pair_record, render_job
from .unreal_helpers import BUILD, RENDER_SETTINGS, unreal_asset
from .unreal_pilot_helpers import JOBS, KIND, ready_worker

CARD_KIND_REFUSED = "'unreal.card_kind' is set by sequence renders only."
PILOT_FIXED = "A pilot job's render_settings cannot be changed."
REPORT = {'job_peak_mb': 9000}
RICH = Target(BUILD, RENDER_SETTINGS['unreal.map'], RENDER_SETTINGS['unreal.sequence'],
              RENDER_SETTINGS['unreal.preset'], (1920, 1080), shot_set_sha256([(0, 120)]), (0,),
              (('motion_blur', False), ('temporal_samples', 1)))


@pytest.fixture
def uasset(db):
    return unreal_asset()


@pytest.fixture
def pilot(uasset, worker_with_token):
    """A targeted pilot whose ``render_settings`` carry every manager-only key of a sequence key."""
    worker, _client = worker_with_token
    ready_worker(worker)
    job = locked_create(uasset, RICH, worker).pilot_job
    job.refresh_from_db()
    assert job.render_settings['unreal.card_kind'] == KIND
    assert job.render_settings['unreal.draft'] == {'motion_blur': False, 'temporal_samples': 1}
    return job


def _patch(client, job, body):
    return client.patch(f'{JOBS}{job.pk}/', body, format='json')


@pytest.mark.django_db
class TestResentSettings:

    def test_identical_settings_pass_and_keep_the_stored_value(self, admin_client, pilot):
        stored, key = copy.deepcopy(pilot.render_settings), pilot.unreal_target_sha256
        resp = _patch(admin_client, pilot, {'render_settings': copy.deepcopy(stored)})
        assert resp.status_code == 200, resp.data
        pilot.refresh_from_db()
        assert (pilot.render_settings, pilot.unreal_target_sha256) == (stored, key)

    def test_key_order_does_not_matter(self, admin_client, pilot):
        stored = copy.deepcopy(pilot.render_settings)
        reordered = dict(reversed(list(stored.items())))
        reordered['unreal.draft'] = dict(reversed(list(stored['unreal.draft'].items())))
        assert list(reordered) != list(stored)
        resp = _patch(admin_client, pilot, {'render_settings': reordered})
        assert resp.status_code == 200, resp.data
        pilot.refresh_from_db()
        assert pilot.render_settings == stored

    @pytest.mark.parametrize('key, value', [
        ('unreal.draft', {'motion_blur': False, 'temporal_samples': 1.0}),
        ('unreal.draft', {'motion_blur': False, 'temporal_samples': True}),
        ('unreal.draft', {'motion_blur': 0, 'temporal_samples': 1}),
        ('unreal.resolution', [1920.0, 1080.0]),
        ('unreal.pilot_frames', [0.0]),
    ], ids=['int-as-float', 'int-as-bool', 'bool-as-int', 'size-as-floats', 'frames-as-floats'])
    def test_a_value_of_another_type_is_a_change(self, admin_client, pilot, key, value):
        stored = copy.deepcopy(pilot.render_settings)
        assert key in stored
        resp = _patch(admin_client, pilot, {'render_settings': {**stored, key: value}})
        assert resp.status_code == 400, resp.data
        assert CARD_KIND_REFUSED in resp.data['render_settings']
        pilot.refresh_from_db()
        assert pilot.render_settings == stored

    def test_a_changed_card_kind_is_refused(self, admin_client, pilot):
        """A real change of a pilot's settings meets the pilot rule (FR-PM-9) first."""
        stored = copy.deepcopy(pilot.render_settings)
        resp = _patch(admin_client, pilot, {'render_settings': {**stored, 'unreal.card_kind': '1002:744c:00c8'}})
        assert (resp.status_code, resp.data) == (400, {'render_settings': [PILOT_FIXED]})
        pilot.refresh_from_db()
        assert pilot.render_settings == stored


def _with_report(asset, worker, status):
    job = render_job(asset)
    pair_record(asset, job.unreal_target_sha256, worker, verdict='fits')
    Job.objects.filter(pk=job.pk).update(status=status, unreal_memory_report=dict(REPORT))
    job.refresh_from_db()
    return job


@pytest.mark.django_db
class TestQueuedAgainClearsTheReport:

    @pytest.mark.parametrize('status', [JobStatus.ERROR, JobStatus.CANCELED])
    def test_admin_patch(self, admin_client, uasset, worker_with_token, status):
        job = _with_report(uasset, ready_worker(worker_with_token[0]), status)
        resp = _patch(admin_client, job, {'status': 'QUEUED'})
        assert resp.status_code == 200, resp.data
        job.refresh_from_db()
        assert (job.status, job.unreal_memory_report) == ('QUEUED', None)

    @pytest.mark.parametrize('status', [JobStatus.ERROR, JobStatus.CANCELED])
    def test_admin_put(self, admin_client, uasset, worker_with_token, status):
        job = _with_report(uasset, ready_worker(worker_with_token[0]), status)
        body = {'name': job.name, 'asset_id': uasset.pk, 'output_file_pattern': job.output_file_pattern,
                'start_frame': job.start_frame, 'end_frame': job.end_frame,
                'render_settings': copy.deepcopy(job.render_settings), 'status': 'QUEUED'}
        resp = admin_client.put(f'{JOBS}{job.pk}/', body, format='json')
        assert resp.status_code == 200, resp.data
        job.refresh_from_db()
        assert (job.status, job.unreal_memory_report) == ('QUEUED', None)

    def test_a_patch_without_a_status_keeps_the_report(self, admin_client, uasset, worker_with_token):
        job = _with_report(uasset, ready_worker(worker_with_token[0]), JobStatus.ERROR)
        resp = _patch(admin_client, job, {'name': 'MtRenamed'})
        assert resp.status_code == 200, resp.data
        job.refresh_from_db()
        assert (job.name, job.status, job.unreal_memory_report) == ('MtRenamed', 'ERROR', REPORT)
