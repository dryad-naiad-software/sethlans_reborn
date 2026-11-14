# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: Unreal projects through the API (Unreal foundation
FR-MU-8, FR-MU-12, FR-MR-11, FR-T-3).
"""

import pytest

from workers.models import Project, SupportedBlenderVersion

from .unreal_helpers import BUILD, unreal_project

URL = '/api/projects/'


def _create(client, **body):
    return client.post(URL, {'name': 'UnrealProj', 'renderer': 'UNREAL', 'engine_build': BUILD, **body},
                       format='json')


@pytest.mark.django_db
class TestCreate:

    def test_succeeds_with_zero_blender_versions(self, admin_client):
        SupportedBlenderVersion.objects.all().delete()
        resp = _create(admin_client)
        assert resp.status_code == 201, resp.data
        assert resp.data['blender_version'] is None
        assert resp.data['blender_version_details'] is None
        assert (resp.data['engine_build'], resp.data['unreal_cvar_overrides']) == (BUILD, {})

    @pytest.mark.parametrize('build', ['5.7.4-51494982', '5.8.4', '5.8.4-0', 'junk', '', '05.8.4-1'])
    def test_rejects_unsupported_builds(self, admin_client, build):
        resp = _create(admin_client, engine_build=build)
        assert resp.status_code == 400
        assert resp.data == {'engine_build': [f"Unreal Engine 5.8.x builds only (got '{build}')."]}

    def test_build_is_required(self, admin_client):
        resp = admin_client.post(URL, {'name': 'NoBuild', 'renderer': 'UNREAL'}, format='json')
        assert resp.status_code == 400
        assert resp.data == {'engine_build': ["Unreal Engine 5.8.x builds only (got '')."]}

    def test_blender_version_is_not_applicable(self, admin_client, default_version):
        resp = _create(admin_client, blender_version=default_version.pk)
        assert resp.status_code == 400
        assert resp.data == {'blender_version': ["Not applicable to Unreal projects."]}

    def test_explicit_null_blender_version_is_a_field_error(self, admin_client):
        resp = _create(admin_client, blender_version=None)
        assert resp.status_code == 400
        assert resp.data == {'blender_version': ['This field may not be null.']}

    def test_cvars_are_normalized(self, admin_client):
        resp = _create(admin_client, unreal_cvar_overrides={'r.Streaming.PoolSize': 3000})
        assert resp.status_code == 201, resp.data
        assert resp.data['unreal_cvar_overrides'] == {'r.Streaming.PoolSize': '3000'}

    @pytest.mark.parametrize('cvars', [
        {'nodot': '1'}, {'r.x_abslog': '1'}, {'x.ExecCmds': '1'}, {'r.Foo': 'a b'},
        {'r.Streaming.PoolSize': 'big'}, {'r.Streaming.PoolSize': '-5'}, ['r.Foo'],
    ])
    def test_rejects_bad_cvars(self, admin_client, cvars):
        resp = _create(admin_client, unreal_cvar_overrides=cvars)
        assert resp.status_code == 400
        assert list(resp.data) == ['unreal_cvar_overrides']


@pytest.mark.django_db
class TestUpdate:

    def test_empty_build_patch_is_400_not_500(self, admin_client):
        project = unreal_project()
        resp = admin_client.patch(f'{URL}{project.pk}/', {'engine_build': ''}, format='json')
        assert resp.status_code == 400
        assert resp.data == {'engine_build': ["Unreal Engine 5.8.x builds only (got '')."]}

    def test_renderer_is_immutable(self, admin_client):
        project = unreal_project()
        resp = admin_client.patch(f'{URL}{project.pk}/', {'renderer': 'BLENDER'}, format='json')
        assert resp.status_code == 400
        assert resp.data == {'renderer': ["Renderer cannot be changed after creation."]}

    def test_rename_and_cvar_edit(self, admin_client):
        project = unreal_project()
        resp = admin_client.patch(f'{URL}{project.pk}/', {
            'name': 'Renamed', 'unreal_cvar_overrides': {'r.RayTracing.ResidentGeometryMemoryPoolSizeInMB': 1024},
        }, format='json')
        assert resp.status_code == 200, resp.data
        project.refresh_from_db()
        assert (project.name, project.unreal_cvar_overrides) == (
            'Renamed', {'r.RayTracing.ResidentGeometryMemoryPoolSizeInMB': '1024'},
        )

    def test_blender_version_patch_is_refused(self, admin_client, default_version):
        project = unreal_project()
        resp = admin_client.patch(f'{URL}{project.pk}/', {'blender_version': default_version.pk}, format='json')
        assert resp.status_code == 400
        assert Project.objects.get(pk=project.pk).blender_version is None
