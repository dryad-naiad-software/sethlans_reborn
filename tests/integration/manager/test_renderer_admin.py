# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Integration tests: Django admin guards for the renderer fields (chunk 1
review follow-up).

* ``JobAdmin`` shows ``renderer`` and ``engine_build`` read-only: the
  first is derived from the project, the second pinned at submission.
* ``AssetAdminForm`` requires a file for ``blend_file`` assets, which the
  model alone no longer does (``blend_file`` is ``blank=True``).
"""

import pytest
from django.contrib import admin
from django.core.files.uploadedfile import SimpleUploadedFile

from workers.admin import AssetAdmin, AssetAdminForm
from workers.models import Asset, Job

VALID_BLEND = b'BLENDER' + bytes(57)


def _form(project, **data):
    files = data.pop('files', {})
    return AssetAdminForm(data={
        'name': 'AdminAsset', 'project': project.pk, 'source_type': 'blend_file',
        'archive_path': '', 'uproject_path': '', 'archive_sha256': '',
        'overlay_path': '', 'overlay_sha256': '', **data,
    }, files=files)


def test_job_admin_renderer_fields_are_read_only():
    job_admin = admin.site._registry[Job]
    assert {'renderer', 'engine_build'} <= set(job_admin.readonly_fields)


def test_asset_admin_uses_the_checking_form():
    assert admin.site._registry[Asset].form is AssetAdminForm
    assert isinstance(admin.site._registry[Asset], AssetAdmin)


@pytest.mark.django_db
class TestAssetAdminForm:

    def test_blend_file_asset_without_a_file_is_rejected(self, project):
        form = _form(project)
        assert not form.is_valid()
        assert form.errors['blend_file'] == ["A .blend file asset requires a file."]

    def test_blend_file_asset_with_a_file_is_valid(self, project, settings, tmp_path):
        settings.MEDIA_ROOT = str(tmp_path)
        upload = SimpleUploadedFile('scene.blend', VALID_BLEND)
        form = _form(project, files={'blend_file': upload})
        assert form.is_valid(), form.errors

    def test_shared_project_asset_needs_no_file(self, project):
        form = _form(project, source_type='shared_project')
        form.is_valid()
        assert 'blend_file' not in form.errors
