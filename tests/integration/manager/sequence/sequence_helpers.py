# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shared helpers for the Unreal sequence integration tests (chunk 2: shot
discovery).  Not a test module.  Paths and names are made up.
"""

from workers.models import Job, SequenceDiscovery

from ..unreal_pilot_helpers import JOBS, POLL, claim, ready_worker

DISCOVERIES = '/api/sequence-discoveries/'
MAP = '/Game/Maps/Harbor.Harbor'
SEQUENCE = '/Game/Cinematics/Ep01.Ep01'
PRESET = '/Game/Presets/Farm.Farm'


def discovery_body(asset, **overrides):
    body = {'asset': asset.pk, 'map': MAP, 'sequence': SEQUENCE, 'preset': PRESET}
    body.update(overrides)
    return body


def start_discovery(admin_client, asset, expect=201, **overrides):
    """POST a discovery; return ``(record, response)``."""
    resp = admin_client.post(DISCOVERIES, discovery_body(asset, **overrides), format='json')
    assert resp.status_code == expect, resp.data
    return SequenceDiscovery.objects.get(pk=resp.data['id']), resp


def shots_job_of(discovery):
    discovery.refresh_from_db()
    return Job.objects.get(pk=discovery.job_id)


def claimed_shots_job(admin_client, asset, worker, worker_client, source='override'):
    """Start a discovery and claim its shots job (no card reading by default)."""
    discovery, _ = start_discovery(admin_client, asset)
    ready_worker(worker, source=source)
    job = shots_job_of(discovery)
    resp = claim(worker_client, job, worker)
    assert resp.status_code == 200, resp.data
    job.refresh_from_db()
    return discovery, job


def post_shots(client, job_pk, body):
    return client.post(f'{JOBS}{job_pk}/shots-result/', body, format='json')


def poll_ids(client):
    resp = client.get(JOBS, POLL)
    assert resp.status_code == 200, resp.data
    return [row['id'] for row in resp.data]
