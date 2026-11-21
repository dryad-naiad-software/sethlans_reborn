# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Every admin route of sequence rendering refuses other callers (Unreal
sequence spec FR-SL-5, AC-21, AC-39).

Each route of FR-SL-5 -- submission list, detail, create, ``DELETE``,
``cancel/``, ``rerender/``, ``merge-sound/``, ``retry-video/``,
``retry-sound/``, every download, and the discovery list, detail and
create -- is called by an anonymous client (401 or 403), a signed-in
non-staff user (403) and a worker token (403); nothing changes.
"""

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from workers.models import SequenceDiscovery, SequenceSubmission

from ..unreal_helpers import unreal_asset
from .sequence_helpers import DISCOVERIES
from .submission_helpers import SUBMISSIONS, submission_body, submit

ROUTES = [
    ('get', 'sub', ''), ('post', 'sub', 'create'), ('get', 'sub', '{s}/'), ('delete', 'sub', '{s}/'),
    ('post', 'sub', '{s}/cancel/'), ('post', 'sub', '{s}/shots/1/rerender/'), ('post', 'sub', '{s}/merge-sound/'),
    ('post', 'sub', '{s}/retry-video/'), ('post', 'sub', '{s}/retry-sound/'),
    ('get', 'sub', '{s}/download-video/'), ('get', 'sub', '{s}/download-video/?sound=1'),
    ('get', 'sub', '{s}/download-sound/'), ('get', 'sub', '{s}/download-frames/'),
    ('get', 'sub', '{s}/shots/1/download-video/'), ('get', 'sub', '{s}/shots/1/download-video/?sound=1'),
    ('get', 'sub', '{s}/shots/1/download-sound/'), ('get', 'sub', '{s}/shots/1/download-frames/'),
    ('get', 'disc', ''), ('post', 'disc', 'create'), ('get', 'disc', '{d}/'),
]
ROUTE_IDS = [f"{method}-{base}-{path or 'list'}".replace('/', '_').replace('?', '_') for method, base, path in ROUTES]


@pytest.fixture
def setup(admin_client, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path / 'media')
    asset = unreal_asset()
    submission, _ = submit(admin_client, asset)
    return asset, submission


def _caller(kind, worker_with_token):
    if kind == 'anonymous':
        return APIClient()
    if kind == 'worker':
        return worker_with_token[1]
    client = APIClient()
    client.force_authenticate(get_user_model().objects.create_user('plain-viewer', password='pw-12345678'))
    return client


def _call(client, method, base, path, asset, submission):
    root = SUBMISSIONS if base == 'sub' else DISCOVERIES
    if path == 'create':
        return client.post(root, submission_body(asset), format='json')
    url = root + path.format(s=submission.pk, d=submission.discovery_id)
    return getattr(client, method)(url)


@pytest.mark.django_db
@pytest.mark.parametrize('kind', ['anonymous', 'non-staff', 'worker'])
@pytest.mark.parametrize('method, base, path', ROUTES, ids=ROUTE_IDS)
def test_refused(setup, worker_with_token, kind, method, base, path):
    asset, submission = setup
    resp = _call(_caller(kind, worker_with_token), method, base, path, asset, submission)
    allowed = (401, 403) if kind == 'anonymous' else (403,)
    assert resp.status_code in allowed
    assert SequenceSubmission.objects.filter(pk=submission.pk, status='DISCOVERING', cancel_requested=False).exists()
    assert SequenceSubmission.objects.count() == 1 and SequenceDiscovery.objects.count() == 1
