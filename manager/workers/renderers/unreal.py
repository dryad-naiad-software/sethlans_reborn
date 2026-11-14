# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal Engine renderer strategy -- stub that rejects (chunk 1).

Unreal foundation spec, Implementation Order chunk 1: the registry, the
data model and the serializer wiring land first, with this strategy
refusing every Unreal request.  Chunk 2 replaces the bodies with the
FR-MU-8..11, FR-RT-3/4/9 and FR-MS-5 rules.

Until then:

* project, asset and job validation return HTTP 400;
* a worker poll that asks for ``UNREAL`` gets no Unreal job (the Blender
  part of the poll is unaffected, so an Unreal-enabled worker keeps
  rendering Blender jobs);
* a claim of an Unreal job (only creatable through the ORM) gets 409;
* the flag methods return the conservative (Blender) values.
"""

from django.db.models import Q
from rest_framework.exceptions import ValidationError

from ..constants import AssetSourceType, Renderer
from .base import RendererStrategy

NOT_SUPPORTED_MESSAGE = (
    "Unreal Engine rendering is not supported by this manager version yet."
)

# FR-RT-14's Unreal worker PATCH allowlist, applied on top of (never
# instead of) ``WORKER_PATCH_FIELDS`` (#217), which bounds every worker
# PATCH.  The spec's set also lists ``started_at``; #217 does not allow it
# (the worker never sends it), so it is left out here.  A unit test keeps
# this a subset of ``WORKER_PATCH_FIELDS``.
UNREAL_WORKER_PATCH_FIELDS = frozenset({
    'status', 'last_output', 'error_message', 'completed_at',
    'render_time_seconds',
})


def _reject(field):
    raise ValidationError({field: [NOT_SUPPORTED_MESSAGE]})


class UnrealStrategy(RendererStrategy):
    """Rules for Unreal projects, shared-project assets and Unreal jobs."""

    renderer = Renderer.UNREAL
    source_type = AssetSourceType.SHARED_PROJECT
    label = 'Unreal'

    def validate_project(self, attrs, instance):
        _reject('renderer')

    def prepare_project_create(self, validated):
        _reject('renderer')

    def validate_asset(self, attrs, instance):
        _reject('source_type')

    def validate_job(self, attrs, asset, instance):
        _reject('asset_id')

    def prepare_job_create(self, validated, asset):
        _reject('asset_id')

    def poll_q(self, params):
        # Matches nothing; OR-ed with the Blender Q it drops out.
        return Q(pk__in=[])

    def claim_rejection(self, worker, job):
        return NOT_SUPPORTED_MESSAGE

    def routing_note(self, job, context):
        return None

    def describe_asset(self, asset):
        return f"Archive: {asset.archive_path} ({asset.uproject_path})"

    def supports(self, feature):
        return False

    def idempotent_reclaim(self):
        return False

    def worker_patch_allowed_fields(self):
        return UNREAL_WORKER_PATCH_FIELDS

    def requeue_on_worker_restart(self):
        return False

    def supports_release(self):
        return False
