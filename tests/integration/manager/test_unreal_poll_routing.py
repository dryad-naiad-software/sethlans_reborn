# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: Unreal jobs in worker polls (Unreal foundation FR-RT-1..3,
FR-MS-5 head-of-line, FR-T-3, AC-5, AC-44b).
"""

import pytest

from workers.models import Job

from .unreal_helpers import BUILD, SHARE, caps, set_share, unreal_asset

URL = '/api/jobs/'
POLL = {'status': 'QUEUED', 'assigned_worker__isnull': 'true'}
UNREAL = {**POLL, 'renderers': 'BLENDER,UNREAL', 'unreal_builds': BUILD}


@pytest.fixture
def jobs(asset):
    blender = Job.objects.create(name='PollBlender', asset=asset, output_file_pattern='//r/b.png')
    unreal = Job.objects.create(name='PollUnreal', asset=unreal_asset(), output_file_pattern='//r/u.png')
    return blender, unreal


@pytest.fixture
def ready_worker(worker_with_token):
    """A worker whose stored capabilities report the build and the manager's share."""
    worker, client = worker_with_token
    set_share(SHARE)
    worker.unreal_capabilities = caps()
    worker.save(update_fields=['unreal_capabilities'])
    return worker, client


def _names(resp):
    assert resp.status_code == 200, resp.data
    return sorted(job['name'] for job in resp.data)


def _set_caps(worker, **kwargs):
    worker.unreal_capabilities = caps(**kwargs)
    worker.save(update_fields=['unreal_capabilities'])


@pytest.mark.django_db
class TestRouting:

    def test_without_renderers_only_blender(self, ready_worker, jobs):
        _worker, client = ready_worker
        assert _names(client.get(URL, POLL)) == ['PollBlender']

    def test_matching_build_returns_the_unreal_job(self, ready_worker, jobs):
        _worker, client = ready_worker
        assert _names(client.get(URL, UNREAL)) == ['PollBlender', 'PollUnreal']
        assert _names(client.get(URL, {**UNREAL, 'renderers': 'UNREAL'})) == ['PollUnreal']

    def test_wrong_build_excludes_it(self, ready_worker, jobs):
        worker, client = ready_worker
        _set_caps(worker, builds=(BUILD, '5.8.4-1'))
        assert _names(client.get(URL, {**UNREAL, 'unreal_builds': '5.8.4-1'})) == ['PollBlender']

    def test_unreal_builds_is_ignored_without_unreal(self, ready_worker, jobs):
        _worker, client = ready_worker
        assert _names(client.get(URL, {**POLL, 'unreal_builds': 'junk'})) == ['PollBlender']

    def test_non_poll_admin_list_returns_both(self, admin_client, jobs):
        assert _names(admin_client.get(URL)) == ['PollBlender', 'PollUnreal']
        assert _names(admin_client.get(URL, {'renderer': 'UNREAL'})) == ['PollUnreal']


@pytest.mark.django_db
class TestHeadOfLineExclusions:
    """The poll leaves out what the claim would refuse (FR-MS-5)."""

    def test_share_unset(self, ready_worker, jobs):
        _worker, client = ready_worker
        set_share('')
        assert _names(client.get(URL, UNREAL)) == ['PollBlender']

    @pytest.mark.parametrize('kwargs', [
        {'share': r'\\other.lan\share'}, {'share': ''}, {'reachable': False}, {'enabled': False},
        {'builds': ('5.8.4-1',)}, {'supported': False},
    ])
    def test_worker_capabilities(self, ready_worker, jobs, kwargs):
        worker, client = ready_worker
        _set_caps(worker, **kwargs)
        assert _names(client.get(URL, UNREAL)) == ['PollBlender']

    def test_share_compared_with_shares_match(self, ready_worker, jobs):
        worker, client = ready_worker
        _set_caps(worker, share=SHARE.upper() + '\\')
        assert _names(client.get(URL, UNREAL)) == ['PollBlender', 'PollUnreal']

    def test_admin_poll_gets_only_the_share_rule(self, admin_client, jobs):
        set_share(SHARE)
        assert _names(admin_client.get(URL, UNREAL)) == ['PollBlender', 'PollUnreal']
        set_share('')
        assert _names(admin_client.get(URL, UNREAL)) == ['PollBlender']


@pytest.mark.django_db
class TestValidation:

    @pytest.mark.parametrize('builds, message', [
        ('', "unreal_builds is required when renderers includes UNREAL."),
        ('5.8.4', "Invalid unreal_builds value: 5.8.4"),
        ('5.7.4-51494982', "Invalid unreal_builds value: 5.7.4-51494982"),
        (','.join(f'5.8.{i}-1' for i in range(11)), "Maximum 10 unreal_builds allowed."),
    ])
    def test_bad_builds_are_400(self, ready_worker, builds, message):
        _worker, client = ready_worker
        resp = client.get(URL, {**UNREAL, 'unreal_builds': builds})
        assert resp.status_code == 400
        assert resp.data == [message]

    def test_missing_builds_is_400(self, ready_worker):
        _worker, client = ready_worker
        resp = client.get(URL, {**POLL, 'renderers': 'UNREAL'})
        assert resp.status_code == 400
