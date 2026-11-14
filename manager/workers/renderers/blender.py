# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Blender renderer strategy: a pure delegation of the pre-renderer behavior.

Unreal foundation spec FR-MR-9.  Every message, error key and body shape
here is the one the API returned before the renderer abstraction, which
``tests/integration/manager/test_renderer_blender_invariance.py`` pins.
The only additions are rejections of the Unreal-only keys
(``engine_build``, ``unreal_cvar_overrides``, the archive fields), which
no Blender payload sent before (FR-MU-8, FR-MU-13, FR-MU-4 mirrors).
"""

import re
from pathlib import Path

from django.db.models import Q
from rest_framework.exceptions import ValidationError

from ..constants import AssetSourceType, Renderer
from ..models import SupportedBlenderVersion
from .base import RendererStrategy

VERSION_REGEX = re.compile(r'^\d+\.\d+\.\d+$')
MAX_AVAILABLE_VERSIONS = 20

# Asset fields that only shared-project (Unreal) assets may carry.
ARCHIVE_FIELDS = (
    'archive_path', 'uproject_path', 'archive_sha256',
    'overlay_path', 'overlay_sha256',
)
_SUPPORTED_FEATURES = frozenset({'tiled_job', 'animation'})


class BlenderStrategy(RendererStrategy):
    """Rules for Blender projects, ``.blend`` assets and Blender jobs."""

    renderer = Renderer.BLENDER
    source_type = AssetSourceType.BLEND_FILE
    label = 'Blender'

    def validate_project(self, attrs, instance):
        errors = {}
        if attrs.get('engine_build'):
            errors['engine_build'] = ["Not applicable to Blender projects."]
        if attrs.get('unreal_cvar_overrides', {}) != {}:
            errors['unreal_cvar_overrides'] = ["Not applicable to Blender projects."]
        if errors:
            raise ValidationError(errors)
        return attrs

    def prepare_project_create(self, validated):
        # Formerly ``ProjectViewSet.perform_create``; still raised from
        # ``serializer.create()``, so ordering and the list-shaped body hold.
        if SupportedBlenderVersion.objects.count() == 0:
            raise ValidationError(
                "At least one supported Blender version must be "
                "configured before creating projects."
            )
        # Formerly ``ProjectSerializer.create``.
        if 'blender_version' not in validated:
            default = SupportedBlenderVersion.objects.filter(
                is_default=True,
            ).first()
            if not default:
                raise ValidationError({
                    'blender_version': (
                        "No default Blender version is configured. "
                        "Please create a supported version first."
                    ),
                })
            validated['blender_version'] = default
        return validated

    def validate_asset(self, attrs, instance):
        project = attrs.get('project') or getattr(instance, 'project', None)
        if project is not None and project.renderer != self.renderer:
            raise ValidationError({'source_type': [
                f"{AssetSourceType(self.source_type).label} assets require "
                f"a {self.label} project."
            ]})
        errors = {
            field: ["Not applicable to .blend file assets."]
            for field in ARCHIVE_FIELDS if attrs.get(field)
        }
        if errors:
            raise ValidationError(errors)
        return attrs

    def validate_job(self, attrs, asset, instance):
        # Imported here: importing the serializers package from this
        # registry would be circular (the serializers import the registry).
        # Resolved through ``serializers.jobs`` at call time, where the
        # check lived before, so patches of that name still apply.
        from ..serializers import jobs as job_serializers
        if attrs.get('engine_build'):
            raise ValidationError({'engine_build': ["Not applicable to Blender jobs."]})
        job_serializers.validate_output_pattern_extension(attrs)
        return attrs

    def prepare_job_create(self, validated, asset):
        return validated

    def poll_q(self, params, worker=None):
        """Today's ``available_versions`` series filter, Blender jobs only.

        ``worker`` is ignored, so Blender poll results are unchanged.
        """
        q = Q(renderer=self.renderer)
        param = params.get('available_versions')
        if not param:
            return q

        versions = [v.strip() for v in param.split(',') if v.strip()]
        if len(versions) > MAX_AVAILABLE_VERSIONS:
            raise ValidationError(
                f"Maximum {MAX_AVAILABLE_VERSIONS} versions allowed."
            )
        for v in versions:
            if not VERSION_REGEX.match(v):
                raise ValidationError(f"Invalid version format: {v}")

        # Extract series from full versions for series-level matching
        series_list = list({'.'.join(v.split('.')[:2]) for v in versions})
        return q & (
            Q(blender_version__series__in=series_list)
            | Q(
                blender_version__isnull=True,
                asset__project__blender_version__series__in=series_list,
            )
        )

    def claim_rejection(self, worker, job):
        effective = job.effective_blender_version
        if effective and not worker.has_blender_version(effective.resolved_version):
            return (
                "Worker does not have required Blender "
                f"version {effective.resolved_version} installed."
            )
        return None

    def routing_note(self, job, context):
        return None

    def describe_asset(self, asset):
        return f"File: {Path(asset.blend_file.name).name}"

    def supports(self, feature):
        return feature in _SUPPORTED_FEATURES

    def idempotent_reclaim(self):
        return False

    def worker_patch_allowed_fields(self):
        return None

    def requeue_on_worker_restart(self):
        return False

    def supports_release(self):
        return False
