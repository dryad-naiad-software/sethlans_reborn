# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The request sequence behind the FR-T-2 Blender API snapshot
(``test_renderer_blender_invariance.py``).

``run_scenario`` sends every FR-T-2 request through real ``APIClient``
calls against one database and returns a ``Recorder`` holding the raw
responses (``finish`` normalizes them for the snapshot).  No
request contains a key name the Unreal foundation adds (FR-MR-10).
Rows the API does not create for us (the Blender versions, a paused
job, the fixed ``submitted_at`` values) are set through the ORM.
"""

import json
from datetime import datetime, timedelta, timezone

from django.core.files.uploadedfile import SimpleUploadedFile

from workers.constants import RenderSettings
from workers.models import Job, SupportedBlenderVersion

from .blender_invariance_normalize import (
    build_labels, keyed_by_name, new_key_paths, normalize, strip_new_keys,
)

PROJECTS_URL = '/api/projects/'
ASSETS_URL = '/api/assets/'
JOBS_URL = '/api/jobs/'
TILED_JOBS_URL = '/api/tiled-jobs/'
ANIMATIONS_URL = '/api/animations/'
HEARTBEAT_URL = '/api/heartbeat/'

WORKER_HOSTNAME = 'invariance-host'
VALID_BLEND = b'BLENDER' + b'\x00' * 57

# The two query parameters that make ``GET /api/jobs/`` a worker poll.
POLL = {'status': 'QUEUED', 'assigned_worker__isnull': 'true'}

# Job ``submitted_at`` values are pinned one minute apart, in creation
# order, so the newest-first lists have no ties.
SUBMITTED_BASE = datetime(2025, 1, 1, tzinfo=timezone.utc)


class Recorder:
    """Collects raw responses, then normalizes them once all rows exist."""

    def __init__(self):
        self._raw = {}

    def capture(self, name, response, kind=None, unordered=False):
        """Record *response* under *name* and return its parsed body.

        *kind* selects the id normalization (see ``ID_REFS``).
        *unordered* marks a worker-poll list (D7 documented exception).
        """
        assert name not in self._raw, f'duplicate capture {name}'
        body = json.loads(response.content) if response.content else None
        self._raw[name] = (response.status_code, body, kind, unordered)
        return body

    def raw_new_key_paths(self):
        """Paths of any new key name in the raw bodies, before stripping.

        Empty on pre-change master; regenerating the snapshot requires
        it to be empty, so a regeneration after chunk 1 fails.
        """
        return [
            path for name, (_, body, _, _) in self._raw.items()
            for path in new_key_paths(body, name)
        ]

    def finish(self):
        """Return ``(captures, poll_orders)`` for the snapshot."""
        labels = build_labels()
        captures, poll_orders = {}, {}
        for name, (status, body, kind, unordered) in self._raw.items():
            body = normalize(strip_new_keys(body, kind), kind, labels)
            if unordered:
                poll_orders[name] = [job['name'] for job in body]
                captures[name] = {
                    'status': status, 'compare': 'set-by-name',
                    'body': keyed_by_name(body),
                }
            else:
                captures[name] = {'status': status, 'body': body}
        return captures, poll_orders


def _blend(name='scene.blend', content=VALID_BLEND):
    return SimpleUploadedFile(
        name, content, content_type='application/octet-stream',
    )


def capture_projects(rec, admin):
    """Project creates; returns ``(default_project_id, explicit_project_id)``.

    Migration 0008 seeds a 4.5 row into a fresh database; it is removed
    (inside the test transaction) for the zero-versions guard capture,
    then the scenario's own two versions are created.
    """
    SupportedBlenderVersion.objects.all().delete()
    rec.capture('project_create_zero_versions', admin.post(
        PROJECTS_URL, {'name': 'ProjNoVersions'}, format='json'))

    SupportedBlenderVersion.objects.create(
        series='4.2', resolved_version='4.2.19', is_default=True)
    explicit = SupportedBlenderVersion.objects.create(
        series='4.5', resolved_version='4.5.8', is_default=False)

    default_body = rec.capture('project_create_default_version', admin.post(
        PROJECTS_URL, {'name': 'ProjDefault'}, format='json'), 'project')
    explicit_body = rec.capture('project_create_with_version', admin.post(
        PROJECTS_URL, {'name': 'ProjExplicit', 'blender_version': explicit.pk},
        format='json'), 'project')
    rec.capture('project_create_null_version', admin.post(
        PROJECTS_URL, {'name': 'ProjNullVersion', 'blender_version': None},
        format='json'))
    return default_body['id'], explicit_body['id']


def capture_assets(rec, admin, default_project, explicit_project):
    """Asset creates and PUT; returns ``(main_asset_id, alt_asset_id)``."""
    def post(name, data):
        return rec.capture(name, admin.post(ASSETS_URL, data, format='multipart'), 'asset')

    main = post('asset_create_valid', {
        'name': 'SceneMain', 'project': default_project, 'blend_file': _blend()})
    alt = post('asset_create_valid_explicit_project', {
        'name': 'SceneAlt', 'project': explicit_project, 'blend_file': _blend()})
    post('asset_create_without_blend_file_key', {
        'name': 'SceneNoKey', 'project': default_project})
    post('asset_create_empty_blend_file', {
        'name': 'SceneEmpty', 'project': default_project, 'blend_file': ''})
    post('asset_create_bad_magic_bytes', {
        'name': 'SceneBadMagic', 'project': default_project,
        'blend_file': _blend(content=b'\x00' * 64)})
    post('asset_create_bad_extension', {
        'name': 'SceneBadExt', 'project': default_project,
        'blend_file': _blend(name='scene.png')})
    rec.capture('asset_put_without_file', admin.put(
        f"{ASSETS_URL}{main['id']}/",
        {'name': 'SceneMain', 'project': default_project},
        format='multipart'), 'asset')
    return main['id'], alt['id']


def _job(name, asset_id, **extra):
    return {
        'name': name, 'asset_id': asset_id,
        'output_file_pattern': f'//render/{name}/frame_####.png', **extra,
    }


def capture_jobs(rec, admin, default_project, main_asset, alt_asset):
    """Job, tiled-job and animation creates; pins ``submitted_at``."""
    v45 = SupportedBlenderVersion.objects.get(series='4.5').pk
    rec.capture('job_create_pattern_format_mismatch', admin.post(JOBS_URL, {
        'name': 'JobMismatch', 'asset_id': main_asset,
        'output_file_pattern': '//render/frame_####.jpg',
        'render_settings': {RenderSettings.IMAGE_FILE_FORMAT: 'PNG'},
    }, format='json'))
    rec.capture('job_create', admin.post(JOBS_URL, _job(
        'JobAlpha', main_asset, render_device='GPU'), format='json'), 'job')
    for payload in (
        _job('JobBeta', main_asset, render_device='CPU'),
        _job('JobGamma', main_asset, blender_version=v45),
        _job('JobDelta', alt_asset),
        _job('JobPaused', main_asset),
    ):
        assert admin.post(JOBS_URL, payload, format='json').status_code == 201
    Job.objects.filter(name='JobPaused').update(is_paused=True)

    rec.capture('tiled_job_create', admin.post(TILED_JOBS_URL, {
        'name': 'TiledMain', 'project': default_project, 'asset_id': main_asset,
        'final_resolution_x': 640, 'final_resolution_y': 480,
        'tile_count_x': 2, 'tile_count_y': 2,
    }, format='json'), 'tiledjob')
    rec.capture('animation_create', admin.post(ANIMATIONS_URL, {
        'name': 'AnimMain', 'project': default_project, 'asset_id': main_asset,
        'output_file_pattern': '//render/anim/frame_####.png',
        'start_frame': 1, 'end_frame': 2, 'frame_step': 1,
    }, format='json'), 'animation')
    rec.capture('animation_create_tiled', admin.post(ANIMATIONS_URL, {
        'name': 'AnimTiled', 'project': default_project, 'asset_id': main_asset,
        'output_file_pattern': '//render/tiled/frame_####.png',
        'start_frame': 1, 'end_frame': 1, 'tiling_config': '2x2',
        'render_settings': {
            RenderSettings.RESOLUTION_X: 640, RenderSettings.RESOLUTION_Y: 480,
        },
    }, format='json'), 'animation')

    for index, pk in enumerate(Job.objects.order_by('id').values_list('pk', flat=True)):
        Job.objects.filter(pk=pk).update(
            submitted_at=SUBMITTED_BASE + timedelta(minutes=index))


def capture_heartbeats(rec, worker_client):
    """Full registration, then a periodic heartbeat."""
    rec.capture('heartbeat_registration', worker_client.post(HEARTBEAT_URL, {
        'hostname': WORKER_HOSTNAME, 'os': 'Linux', 'ip_address': '10.0.0.7',
        'available_tools': {'blender': ['4.2.19']}, 'cpu_name': 'Test CPU 8C',
        'ui_url': f'https://{WORKER_HOSTNAME}.lan:7443', 'status': 'IDLE',
    }, format='json'), 'worker')
    rec.capture('heartbeat', worker_client.post(HEARTBEAT_URL, {
        'hostname': WORKER_HOSTNAME, 'status': 'IDLE',
        'ui_url': f'https://{WORKER_HOSTNAME}.lan:7443',
        'schedule': {'enabled': False, 'days': [], 'unknown_key': 1},
    }, format='json'), 'worker')


def capture_polls(rec, worker_client):
    """Worker polls (D7: compared as sets) and their 400 errors."""
    def poll(name, unordered=True, **params):
        rec.capture(name, worker_client.get(JOBS_URL, {**POLL, **params}),
                    'job', unordered)

    poll('poll_without_available_versions')
    poll('poll_with_available_versions', available_versions='4.2.19')
    poll('poll_worker_params', device_prefs='GPU,CPU,ANY',
         available_versions='4.2.19,4.5.8')
    poll('poll_device_cpu_only', device_prefs='CPU')
    poll('poll_invalid_version_format', False, available_versions='4.2')
    poll('poll_too_many_versions', False,
         available_versions=','.join(f'4.{n}.0' for n in range(21)))
    poll('poll_error_precedence', False, device_prefs='BANANA',
         available_versions='not-a-version')


def capture_claims(rec, admin, worker_client, worker_pk):
    """Claim (mismatch and success), worker PATCH, then the admin list."""
    def job_url(name, suffix=''):
        return f'{JOBS_URL}{Job.objects.get(name=name).pk}/{suffix}'

    body = {'worker_id': worker_pk}
    rec.capture('claim_version_mismatch', worker_client.post(
        job_url('JobGamma', 'claim/'), body, format='json'))
    rec.capture('claim_success', worker_client.post(
        job_url('JobAlpha', 'claim/'), body, format='json'), 'job')
    rec.capture('worker_status_patch', worker_client.patch(job_url('JobAlpha'), {
        'status': 'DONE', 'render_time_seconds': 42,
        'last_output': 'Fra:1 Saved frame_0001.png',
    }, format='json'), 'job')
    rec.capture('admin_job_list', admin.get(JOBS_URL), 'job')


def run_scenario(admin, worker_client, worker):
    """Send every FR-T-2 request; return the filled ``Recorder``."""
    rec = Recorder()
    default_project, explicit_project = capture_projects(rec, admin)
    main_asset, alt_asset = capture_assets(
        rec, admin, default_project, explicit_project)
    capture_jobs(rec, admin, default_project, main_asset, alt_asset)
    capture_heartbeats(rec, worker_client)
    capture_polls(rec, worker_client)
    capture_claims(rec, admin, worker_client, worker.pk)
    return rec
