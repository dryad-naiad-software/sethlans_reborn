# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: shared-project (Unreal) assets through the API
(Unreal foundation FR-MU-9, FR-MU-12, FR-MU-15, FR-MR-11, FR-T-3, AC-23, AC-32).
"""

import pytest

from workers.models import Asset, Job, Project
from workers.renderers.unreal_validation import SHARE_UNSET_ASSET_MESSAGE

from .unreal_helpers import ARCHIVE_SHA, OVERLAY_SHA, SHARE, set_share, unreal_asset, unreal_project

URL = '/api/assets/'


@pytest.fixture
def project(db):
    set_share(SHARE)
    return unreal_project()


def _body(project, **extra):
    body = {
        'name': 'UnrealAsset', 'project': str(project.pk), 'source_type': 'shared_project',
        'archive_path': 'incoming/ACOMAnimationSample.zip',
        'uproject_path': 'ACOMAnimationSample/ACOMAnimationSample.uproject',
        'archive_sha256': ARCHIVE_SHA,
    }
    body.update(extra)
    return body


def _post(client, project, **extra):
    return client.post(URL, _body(project, **extra), format='multipart')


@pytest.mark.django_db
class TestCreate:

    def test_stores_normalized_paths_and_lowercase_pins(self, admin_client, project):
        resp = _post(admin_client, project, archive_path=r'incoming\ACOM.zip',
                     overlay_path=r'project-fixes\v1', overlay_sha256=OVERLAY_SHA.upper(),
                     archive_sha256=ARCHIVE_SHA.upper())
        assert resp.status_code == 201, resp.data
        asset = Asset.objects.get(pk=resp.data['id'])
        assert (asset.archive_path, asset.overlay_path) == ('incoming/ACOM.zip', 'project-fixes/v1')
        assert (asset.archive_sha256, asset.overlay_sha256) == (ARCHIVE_SHA, OVERLAY_SHA)
        assert not asset.blend_file
        assert resp.data['blend_file'] is None

    @pytest.mark.parametrize('value', [
        '/incoming/x.zip', 'C:\\x.zip', r'\\host\share\x.zip', '//host/x.zip', r'\\?\C:\x.zip',
        '../x.zip', './x.zip', 'incoming//x.zip', 'CON/x.zip', 'incoming/x.rar',
        'incoming /x.zip', ' incoming/x.zip', 'PROGRA~1/x.zip', 'a\u202eb/x.zip',
        'a\u200bb/x.zip', 'e\u0301/x.zip',
    ])
    def test_rejects_every_relpath_rule(self, admin_client, project, value):
        resp = _post(admin_client, project, archive_path=value)
        assert resp.status_code == 400
        assert list(resp.data) == ['archive_path']

    @pytest.mark.parametrize('value', ['/fixes', '../fixes', 'fixes/', 'C:\\fixes', r'\\h\s\fixes'])
    def test_rejects_bad_overlay_paths(self, admin_client, project, value):
        resp = _post(admin_client, project, overlay_path=value, overlay_sha256=OVERLAY_SHA)
        assert resp.status_code == 400
        assert list(resp.data) == ['overlay_path']

    def test_path_longer_than_512_is_refused(self, admin_client, project):
        long_rel = '/'.join(['d' * 200] * 3) + '.zip'
        resp = _post(admin_client, project, archive_path=long_rel)
        assert resp.status_code == 400
        assert list(resp.data) == ['archive_path']

    def test_share_unset(self, admin_client, project):
        set_share('')
        resp = _post(admin_client, project)
        assert resp.status_code == 400
        assert resp.data == {'archive_path': [SHARE_UNSET_ASSET_MESSAGE]}

    @pytest.mark.parametrize('value', ['/A/A.uproject', 'A\\A.uproject', 'a/b/c/A.uproject', 'A/A.txt',
                                       'A/' + 'B' * 70 + '.uproject', ('a' * 128 + '/') * 2 + 'A.uproject'])
    def test_uproject_rules(self, admin_client, project, value):
        resp = _post(admin_client, project, uproject_path=value)
        assert resp.status_code == 400
        assert list(resp.data) == ['uproject_path']

    def test_archive_pin_is_mandatory(self, admin_client, project):
        body = _body(project)
        del body['archive_sha256']
        resp = admin_client.post(URL, body, format='multipart')
        assert (resp.status_code, resp.data) == (400, {'archive_sha256': ["This field is required."]})
        resp = _post(admin_client, project, archive_sha256='xyz')
        assert list(resp.data) == ['archive_sha256']

    def test_overlay_pin_iff_overlay_path(self, admin_client, project):
        resp = _post(admin_client, project, overlay_path='fixes')
        assert resp.data == {'overlay_sha256': ["Required when overlay_path is set."]}
        resp = _post(admin_client, project, overlay_sha256=OVERLAY_SHA)
        assert resp.data == {'overlay_sha256': ["Not allowed without overlay_path."]}

    def test_renderer_and_source_mismatch(self, admin_client, project, default_version):
        blender = Project.objects.create(name='BlenderProj', blender_version=default_version)
        resp = _post(admin_client, blender)
        assert (resp.status_code, list(resp.data)) == (400, ['source_type'])


@pytest.mark.django_db
class TestUpdate:

    def test_move_to_a_blender_project(self, admin_client, project, default_version):
        asset = unreal_asset(project)
        blender = Project.objects.create(name='BlenderProj2', blender_version=default_version)
        resp = admin_client.patch(f'{URL}{asset.pk}/', {'project': str(blender.pk)}, format='multipart')
        assert resp.status_code == 400
        assert list(resp.data) == ['project']

    def test_delivery_fields_frozen_once_a_job_exists(self, admin_client, project):
        asset = unreal_asset(project)
        resp = admin_client.patch(f'{URL}{asset.pk}/', {'archive_path': 'incoming/v2.zip'}, format='multipart')
        assert resp.status_code == 200, resp.data
        Job.objects.create(name='Frozen', asset=asset, output_file_pattern='//r/f.png')
        resp = admin_client.patch(f'{URL}{asset.pk}/', {'archive_path': 'incoming/v3.zip'}, format='multipart')
        assert resp.status_code == 400
        assert resp.data == {'archive_path': ["Cannot change archive_path while jobs reference this asset."]}

    def test_rename_needs_no_share(self, admin_client, project):
        asset = unreal_asset(project)
        set_share('')
        resp = admin_client.patch(f'{URL}{asset.pk}/', {'name': 'Renamed'}, format='multipart')
        assert resp.status_code == 200, resp.data

    def test_overlay_pair_on_effective_values(self, admin_client, project):
        asset = unreal_asset(project)
        resp = admin_client.patch(f'{URL}{asset.pk}/', {'overlay_path': 'fixes'}, format='multipart')
        assert resp.data == {'overlay_sha256': ["Required when overlay_path is set."]}
        resp = admin_client.patch(f'{URL}{asset.pk}/', {'overlay_path': 'fixes', 'overlay_sha256': OVERLAY_SHA},
                                  format='multipart')
        assert resp.status_code == 200, resp.data
