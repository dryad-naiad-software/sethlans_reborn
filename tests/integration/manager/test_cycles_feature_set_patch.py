# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: updates cannot leave a job EXPERIMENTAL on Blender 5.0+ (#244).

The pair checked is the one the row has after the PATCH: the sent values
over the stored ones, with the version the row really renders with (its
own ``blender_version``, else its asset's project's). Jobs, tiled jobs and
animations all accept ``cycles_feature_set``, ``blender_version`` and
``asset_id`` on PATCH.
"""

import pytest

from .cycles_feature_set_helpers import (
    KINDS, blend_asset, created_pk, expected_message, feature_set_error, patch, post, version,
)

ALL_KINDS = list(KINDS)


@pytest.fixture
def asset52(db):
    """An asset whose project renders with Blender 5.2."""
    return blend_asset('5.2', 'Asset52')


def _row(kind, pk):
    return KINDS[kind][2].objects.select_related('blender_version').get(pk=pk)


def _pair(kind, pk):
    row = _row(kind, pk)
    series = row.blender_version.series if row.blender_version else None
    return row.cycles_feature_set, series


@pytest.mark.django_db
class TestVersionChange:

    @pytest.mark.parametrize('kind', ALL_KINDS)
    @pytest.mark.parametrize('series', ['5.0', '5.2'])
    def test_to_5x_while_experimental_refused(self, admin_client, asset, kind, series):
        pk = created_pk(post(admin_client, kind, asset, cycles_feature_set='EXPERIMENTAL'))
        resp = patch(admin_client, kind, pk, {'blender_version': version(series).pk})
        assert feature_set_error(resp) == expected_message(series)
        assert _pair(kind, pk) == ('EXPERIMENTAL', None)

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_to_5x_with_supported_allowed(self, admin_client, asset, kind):
        pk = created_pk(post(admin_client, kind, asset, cycles_feature_set='EXPERIMENTAL'))
        resp = patch(admin_client, kind, pk, {
            'blender_version': version('5.2').pk, 'cycles_feature_set': 'SUPPORTED',
        })
        assert resp.status_code == 200, resp.data
        assert _pair(kind, pk) == ('SUPPORTED', '5.2')

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_to_4x_on_5x_project_while_experimental_allowed(self, admin_client, asset52, kind):
        pk = created_pk(post(admin_client, kind, asset52, cycles_feature_set='SUPPORTED'))
        resp = patch(admin_client, kind, pk, {
            'blender_version': version('4.5').pk, 'cycles_feature_set': 'EXPERIMENTAL',
        })
        assert resp.status_code == 200, resp.data
        assert _pair(kind, pk) == ('EXPERIMENTAL', '4.5')

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_clearing_4x_override_on_5x_project_refused(self, admin_client, asset52, kind):
        # Without its own version the row falls back to the project's 5.2.
        pk = created_pk(post(admin_client, kind, asset52, cycles_feature_set='EXPERIMENTAL',
                             blender_version=version('4.5').pk))
        resp = patch(admin_client, kind, pk, {'blender_version': None})
        assert feature_set_error(resp) == expected_message('5.2')
        assert _pair(kind, pk) == ('EXPERIMENTAL', '4.5')

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_5x_to_4x_override_keeps_supported(self, admin_client, asset52, kind):
        pk = created_pk(post(admin_client, kind, asset52))
        resp = patch(admin_client, kind, pk, {'blender_version': version('4.2').pk})
        assert resp.status_code == 200, resp.data
        assert _pair(kind, pk) == ('SUPPORTED', '4.2')


@pytest.mark.django_db
class TestFeatureSetChange:

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_experimental_on_5x_project_row_refused(self, admin_client, asset52, kind):
        pk = created_pk(post(admin_client, kind, asset52, cycles_feature_set='SUPPORTED'))
        resp = patch(admin_client, kind, pk, {'cycles_feature_set': 'EXPERIMENTAL'})
        assert feature_set_error(resp) == expected_message('5.2')
        assert _pair(kind, pk) == ('SUPPORTED', None)

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_experimental_on_5x_job_version_row_refused(self, admin_client, asset, kind):
        pk = created_pk(post(admin_client, kind, asset, blender_version=version('5.0').pk))
        resp = patch(admin_client, kind, pk, {'cycles_feature_set': 'EXPERIMENTAL'})
        assert feature_set_error(resp) == expected_message('5.0')
        assert _pair(kind, pk) == ('SUPPORTED', '5.0')

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_experimental_on_4x_row_allowed(self, admin_client, asset, kind):
        pk = created_pk(post(admin_client, kind, asset))
        resp = patch(admin_client, kind, pk, {'cycles_feature_set': 'EXPERIMENTAL'})
        assert resp.status_code == 200, resp.data
        assert _pair(kind, pk) == ('EXPERIMENTAL', None)

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_supported_on_5x_row_allowed(self, admin_client, asset52, kind):
        pk = created_pk(post(admin_client, kind, asset52))
        resp = patch(admin_client, kind, pk, {'cycles_feature_set': 'SUPPORTED'})
        assert resp.status_code == 200, resp.data


def _move(kind, asset):
    """The PATCH body that moves a row to ``asset`` (parents also name its project)."""
    body = {'asset_id': asset.pk}
    if kind != 'job':
        body['project'] = str(asset.project_id)
    return body


@pytest.mark.django_db
class TestAssetMove:

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_experimental_moved_to_5x_project_asset_refused(self, admin_client, asset, asset52, kind):
        pk = created_pk(post(admin_client, kind, asset, cycles_feature_set='EXPERIMENTAL'))
        resp = patch(admin_client, kind, pk, _move(kind, asset52))
        assert feature_set_error(resp) == expected_message('5.2')
        assert _row(kind, pk).asset_id == asset.pk

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_supported_moved_to_5x_project_asset_allowed(self, admin_client, asset, asset52, kind):
        pk = created_pk(post(admin_client, kind, asset))
        resp = patch(admin_client, kind, pk, _move(kind, asset52))
        assert resp.status_code == 200, resp.data
        assert _row(kind, pk).asset_id == asset52.pk

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_experimental_with_4x_override_moved_to_5x_asset_allowed(self, admin_client, asset, asset52, kind):
        pk = created_pk(post(admin_client, kind, asset, cycles_feature_set='EXPERIMENTAL',
                             blender_version=version('4.5').pk))
        resp = patch(admin_client, kind, pk, _move(kind, asset52))
        assert resp.status_code == 200, resp.data
        assert _row(kind, pk).asset_id == asset52.pk

    @pytest.mark.parametrize('kind', ALL_KINDS)
    def test_experimental_moved_from_5x_to_4x_asset_allowed(self, admin_client, asset, asset52, kind):
        pk = created_pk(post(admin_client, kind, asset52))
        resp = patch(admin_client, kind, pk, {**_move(kind, asset), 'cycles_feature_set': 'EXPERIMENTAL'})
        assert resp.status_code == 200, resp.data
        assert _pair(kind, pk) == ('EXPERIMENTAL', None)
