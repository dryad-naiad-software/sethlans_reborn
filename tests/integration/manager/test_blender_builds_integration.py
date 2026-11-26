# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: the manager's Blender build cache endpoints and the exact-version poll.

Real parts: the routes and their path converters, ``TokenAuthentication`` +
``IsWorker``, the two views, ``services.blender_cache`` (wanted set, row
inserts and re-queues, cache paths) and the test database. The cache root is
a temporary folder (``settings.BLENDER_CACHE_ROOT``). Under pytest no runner
thread starts (fetching is off), so "runner alive" is marked by replacing
``runner.is_alive``: the one thing outside the parts under test.

Covers Blender sources BS-MGR-1, BS-MGR-5, BS-MGR-9..12 and BS-MGR-15.
"""

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from workers.constants import BlenderBuildStatus
from workers.models import Asset, BlenderBuild, Job, Project, SupportedBlenderVersion, Worker
from workers.services.blender_cache import runner

VERSION, PLATFORM = '4.5.8', 'linux-x64'
FILE_NAME = 'blender-4.5.8-linux-x64.tar.xz'
SHA256 = 'ab' * 32
CONTENT = b'not really a tar.xz, but bytes the manager streams unchanged' * 64
META_URL = f'/api/blender-builds/{VERSION}/{PLATFORM}/'
ARCHIVE_URL = f'{META_URL}archive/'


@pytest.fixture(autouse=True)
def _forget_runner():
    """The views wake the runner's module Event; leave no runner state behind."""
    yield
    runner.reset_for_tests()


@pytest.fixture
def cache_root(settings, tmp_path):
    settings.BLENDER_CACHE_ROOT = tmp_path / 'blender'
    return settings.BLENDER_CACHE_ROOT


@pytest.fixture
def worker_client(db, cache_root, make_worker_client):
    """An enrolled linux-x64 worker with 4.5 supported (resolved 4.5.8): 4.5.8 linux-x64 is wanted."""
    SupportedBlenderVersion.objects.update_or_create(series='4.5', defaults={'resolved_version': VERSION})
    worker, client, _token = make_worker_client('bs-linux')
    Worker.objects.filter(pk=worker.pk).update(blender_platform=PLATFORM)
    return client


@pytest.fixture
def runner_alive(monkeypatch):
    monkeypatch.setattr(runner, 'is_alive', lambda: True)


def _ready_row(cache_root, with_file=True):
    if with_file:
        (cache_root / VERSION).mkdir(parents=True)
        (cache_root / VERSION / FILE_NAME).write_bytes(CONTENT)
    return BlenderBuild.objects.create(
        version=VERSION, platform=PLATFORM, file_name=FILE_NAME, sha256=SHA256,
        size=len(CONTENT), status=BlenderBuildStatus.READY,
    )


@pytest.mark.django_db
class TestBlenderBuildEndpointsIntegration:

    def test_ready_build_metadata_and_archive(self, worker_client, cache_root):
        """BS-MGR-10, BS-MGR-11: a worker token gets the metadata, then the file's exact bytes."""
        _ready_row(cache_root)
        meta = worker_client.get(META_URL)
        assert (meta.status_code, meta.json()) == (200, {
            'version': VERSION, 'platform': PLATFORM, 'file_name': FILE_NAME,
            'sha256': SHA256, 'size': len(CONTENT),
        })
        archive = worker_client.get(ARCHIVE_URL)
        try:
            assert archive.status_code == 200
            assert archive['Content-Type'] == 'application/octet-stream'
            assert archive['Content-Length'] == str(len(CONTENT))
            assert f'filename="{FILE_NAME}"' in archive['Content-Disposition']
            assert b''.join(archive.streaming_content) == CONTENT
        finally:
            archive.close()

    @pytest.mark.parametrize('caller, expected', [('anonymous', 401), ('admin_token', 403)])
    def test_non_worker_callers_are_refused(self, worker_client, cache_root, admin_user, caller, expected):
        """Security (BS-MGR-10, BS-MGR-11): only an enrolled worker's token reads metadata or archives."""
        _ready_row(cache_root)
        client = APIClient()
        if caller == 'admin_token':
            client.credentials(HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=admin_user).key}')
        for url in (META_URL, ARCHIVE_URL):
            response = client.get(url)
            assert response.status_code == expected, url

    @pytest.mark.parametrize('path', ['4.5.8/linux-x86/', '4.5/linux-x64/'])
    def test_bad_platform_or_version_is_404_before_the_view(self, worker_client, path):
        """BS-MGR-12: anything but X.Y.Z and the six platform ids is a route 404 and adds no row."""
        response = worker_client.get(f'/api/blender-builds/{path}')
        assert response.status_code == 404
        assert BlenderBuild.objects.count() == 0

    def test_unsupported_version_is_not_wanted_and_adds_no_row(self, worker_client):
        """BS-MGR-5, BS-MGR-10: a worker cannot make the manager keep a build nobody wants."""
        response = worker_client.get(f'/api/blender-builds/4.4.3/{PLATFORM}/')
        assert (response.status_code, response.json()) == (404, {'status': 'not_wanted'})
        assert BlenderBuild.objects.count() == 0

    @pytest.mark.parametrize('alive, expected', [
        (False, (404, {'status': 'not_wanted'})),
        (True, (202, {'status': 'queued'})),
    ])
    def test_wanted_missing_build_gets_a_queued_row(self, worker_client, monkeypatch, alive, expected):
        """BS-MGR-5, BS-MGR-10: the request inserts only that row; 202 only while a runner can fetch it."""
        monkeypatch.setattr(runner, 'is_alive', lambda: alive)
        response = worker_client.get(META_URL)
        assert (response.status_code, response.json()) == expected
        row = BlenderBuild.objects.get()
        assert (row.version, row.platform, row.file_name, row.status) == (
            VERSION, PLATFORM, FILE_NAME, BlenderBuildStatus.QUEUED)

    def test_ready_row_without_its_file_is_queued_again(self, worker_client, cache_root, runner_alive):
        """BS-MGR-9, BS-MGR-11: a READY row whose file is gone answers queued, and its archive is 404."""
        row = _ready_row(cache_root, with_file=False)
        response = worker_client.get(META_URL)
        assert (response.status_code, response.json()) == (202, {'status': 'queued'})
        row.refresh_from_db()
        assert row.status == BlenderBuildStatus.QUEUED
        assert worker_client.get(ARCHIVE_URL).status_code == 404


SUPPORTED_URL = '/api/supported-versions/'


@pytest.mark.django_db(transaction=True)
def test_registration_and_version_changes_reconcile_the_rows(make_worker_client, admin_client, monkeypatch):
    """BS-MGR-1, BS-MGR-5: a full registration stores the reported platform and, like a
    supported-version create or confirmed delete, reconciles the build rows once committed.

    Transactional: the views' ``on_commit`` hooks run for real when their writes commit.
    """
    SupportedBlenderVersion.objects.all().delete()
    SupportedBlenderVersion.objects.create(series='4.5', resolved_version=VERSION, is_default=True)
    worker, client, _token = make_worker_client('bs-register')

    def rows():
        return set(BlenderBuild.objects.values_list('version', 'platform', 'status'))

    response = client.post('/api/heartbeat/', {
        'hostname': worker.hostname, 'os': 'Linux', 'blender_platform': PLATFORM, 'available_tools': {},
    }, format='json')
    assert response.status_code == 200, response.data
    worker.refresh_from_db()
    assert worker.blender_platform == PLATFORM
    assert rows() == {(VERSION, PLATFORM, BlenderBuildStatus.QUEUED)}

    monkeypatch.setattr('workers.serializers.blender_versions.latest_patch', lambda series, timeout: '4.4.3')
    created = admin_client.post(SUPPORTED_URL, {'series': '4.4'}, format='json')
    assert created.status_code == 201, created.data
    assert rows() == {(VERSION, PLATFORM, BlenderBuildStatus.QUEUED), ('4.4.3', PLATFORM, BlenderBuildStatus.QUEUED)}

    deleted = admin_client.delete(f"{SUPPORTED_URL}{created.data['id']}/?confirm=true")
    assert deleted.status_code == 200, deleted.data
    assert rows() == {(VERSION, PLATFORM, BlenderBuildStatus.QUEUED)}


POLL = {'status': 'QUEUED', 'assigned_worker__isnull': 'true'}


@pytest.fixture
def job_on_459(db, default_version, request):
    """One QUEUED Blender job whose effective version is 4.5.9, set on the job or on its project."""
    v459, _ = SupportedBlenderVersion.objects.update_or_create(series='4.5', defaults={'resolved_version': '4.5.9'})
    on_job = request.param == 'job'
    project = Project.objects.create(name='ExactPoll', blender_version=default_version if on_job else v459)
    asset = Asset.objects.create(name='ExactPollAsset', project=project)
    return Job.objects.create(name='Exact459', asset=asset, output_file_pattern='//r/f_####.png',
                              blender_version=v459 if on_job else None)


def _poll_names(client, **params):
    response = client.get('/api/jobs/', {**POLL, **params})
    assert response.status_code == 200, response.data
    return [job['name'] for job in response.data]


@pytest.mark.django_db
@pytest.mark.parametrize('job_on_459', ['job', 'project'], indirect=True)
class TestExactVersionPollIntegration:

    def test_worker_with_another_patch_is_not_handed_the_job(self, worker_with_token, job_on_459):
        """BS-MGR-15: a worker reporting 4.5.8 gets no 4.5.9 job; one reporting 4.5.9 does.

        4.2.19 is in the first poll too: a job's own version wins over its project's.
        """
        _worker, client = worker_with_token
        assert _poll_names(client, available_versions='4.5.8,4.2.19') == []
        assert _poll_names(client, available_versions='4.5.8,4.5.9') == ['Exact459']

    def test_poll_without_available_versions_gets_no_blender_job(self, worker_with_token, job_on_459):
        """BS-MGR-15: a worker poll that reports no versions matches no Blender job."""
        _worker, client = worker_with_token
        assert _poll_names(client) == []
