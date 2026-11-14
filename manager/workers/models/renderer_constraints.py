# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Database check constraints that keep the renderer fields consistent.

Unreal foundation spec FR-MU-4.  Every constraint carries a
``violation_error_message`` so a Django admin ModelForm (which runs
``validate_constraints``) shows a readable error instead of a 500.  DRF
never runs ``validate_constraints``, so each rule is mirrored by a
serializer rule in the renderer strategies (``workers/renderers/``).

A non-empty ``blend_file`` for ``blend_file`` assets stays a serializer
rule, not a database rule, so the migration cannot fail on legacy rows.
"""

from django.db import models
from django.db.models import Q

from ..constants import AssetSourceType, Renderer

_BLENDER = Renderer.BLENDER.value
_UNREAL = Renderer.UNREAL.value
_BLEND_FILE = AssetSourceType.BLEND_FILE.value
_SHARED_PROJECT = AssetSourceType.SHARED_PROJECT.value

PROJECT_RENDERER_FIELDS_CONSISTENT = models.CheckConstraint(
    condition=(
        Q(renderer=_BLENDER, blender_version__isnull=False, engine_build='')
        | (Q(renderer=_UNREAL, blender_version__isnull=True) & ~Q(engine_build=''))
    ),
    name='project_renderer_fields_consistent',
    violation_error_message=(
        "A Blender project needs a Blender version and no engine build; "
        "an Unreal project needs an engine build and no Blender version."
    ),
)

ASSET_SOURCE_FIELDS_CONSISTENT = models.CheckConstraint(
    condition=(
        Q(
            source_type=_BLEND_FILE, archive_path='', uproject_path='',
            overlay_path='', archive_sha256='', overlay_sha256='',
        )
        | (
            Q(source_type=_SHARED_PROJECT, blend_file='')
            & ~Q(archive_path='') & ~Q(uproject_path='') & ~Q(archive_sha256='')
            & (
                Q(overlay_path='', overlay_sha256='')
                | (~Q(overlay_path='') & ~Q(overlay_sha256=''))
            )
        )
    ),
    name='asset_source_fields_consistent',
    violation_error_message=(
        "A .blend file asset cannot carry archive fields; a shared-project asset "
        "needs archive_path, uproject_path and archive_sha256, no .blend file, "
        "and overlay_sha256 exactly when overlay_path is set."
    ),
)

JOB_RENDERER_BUILD_CONSISTENT = models.CheckConstraint(
    condition=(
        Q(renderer=_BLENDER, engine_build='')
        | (Q(renderer=_UNREAL) & ~Q(engine_build=''))
    ),
    name='job_renderer_build_consistent',
    violation_error_message=(
        "A Blender job has no engine build; an Unreal job needs one."
    ),
)

JOB_KIND_CONSISTENT = models.CheckConstraint(
    condition=Q(unreal_kind='render') | Q(renderer=_UNREAL),
    name='job_kind_consistent',
    violation_error_message="Only an Unreal job can be a pilot job.",
)
