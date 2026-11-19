# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the Cycles feature set integration tests (#244).

Not a test module: imported by ``test_cycles_feature_set_*.py``.
"""

from django.core.files.base import ContentFile

from workers.constants import RenderSettings
from workers.models import Animation, Asset, Job, Project, SupportedBlenderVersion, TiledJob

JOBS_URL = '/api/jobs/'
TILED_JOBS_URL = '/api/tiled-jobs/'
ANIMATIONS_URL = '/api/animations/'
FEATURE_SET_KEY = RenderSettings.CYCLES_FEATURE_SET

RESOLVED = {'4.2': '4.2.19', '4.5': '4.5.8', '5.0': '5.0.1', '5.2': '5.2.0'}


def version(series):
    """The ``SupportedBlenderVersion`` row of ``series`` (the migration may seed some)."""
    row, _ = SupportedBlenderVersion.objects.get_or_create(
        series=series, defaults={'resolved_version': RESOLVED[series]},
    )
    return row


def blend_asset(series, name):
    """A ``.blend`` asset in a new Blender project whose version is ``series``."""
    project = Project.objects.create(name=f'Proj{name}', blender_version=version(series))
    return Asset.objects.create(
        project=project, name=name,
        blend_file=ContentFile(b'BLENDER' + b'\x00' * 57, name='test.blend'),
    )


def job_payload(asset, **extra):
    body = {
        'name': 'FeatureSetJob', 'asset_id': asset.pk,
        'output_file_pattern': 'render_####.png', 'start_frame': 1, 'end_frame': 1,
    }
    body.update(extra)
    return body


def tiled_payload(asset, **extra):
    body = {
        'name': 'FeatureSetTiled', 'project': str(asset.project_id), 'asset_id': asset.pk,
        'final_resolution_x': 800, 'final_resolution_y': 600,
        'tile_count_x': 2, 'tile_count_y': 2,
    }
    body.update(extra)
    return body


def animation_payload(asset, **extra):
    body = {
        'name': 'FeatureSetAnim', 'project': str(asset.project_id), 'asset_id': asset.pk,
        'output_file_pattern': 'anim_####.png', 'start_frame': 1, 'end_frame': 2,
        'frame_step': 1, 'tiling_config': 'NONE',
    }
    body.update(extra)
    return body


# kind -> (endpoint, payload builder, model)
KINDS = {
    'job': (JOBS_URL, job_payload, Job),
    'tiled_job': (TILED_JOBS_URL, tiled_payload, TiledJob),
    'animation': (ANIMATIONS_URL, animation_payload, Animation),
}


def post(client, kind, asset, **extra):
    url, payload, _ = KINDS[kind]
    return client.post(url, data=payload(asset, **extra), format='json')


def patch(client, kind, pk, data):
    url, _, _ = KINDS[kind]
    return client.patch(f'{url}{pk}/', data=data, format='json')


def created_pk(resp):
    assert resp.status_code == 201, resp.data
    return resp.data['id']


def feature_set_error(resp):
    """The single ``cycles_feature_set`` message of a 400 response."""
    assert resp.status_code == 400, resp.data
    assert list(resp.data) == ['cycles_feature_set'], resp.data
    assert len(resp.data['cycles_feature_set']) == 1
    return str(resp.data['cycles_feature_set'][0])


def expected_message(series):
    return (
        "The 'EXPERIMENTAL' Cycles feature set is not available in Blender 5.0 "
        f"and later (this job uses Blender {series}). Use 'SUPPORTED'."
    )
