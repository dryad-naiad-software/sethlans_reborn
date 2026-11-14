# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal Engine renderer strategy (Unreal foundation FR-MR-8, chunk 2).

Phase 1 renders one Unreal still per job from a project archive on the
farm's Sethlans share.  The rules live in three modules this class
delegates to:

* ``unreal_validation`` -- projects (exact supported build, CVars) and
  shared-project assets (share-relative paths, mandatory pins);
* ``unreal_settings`` -- the strict ``render_settings`` schema and the
  single-frame still rules;
* ``unreal_routing`` -- the worker-poll filter, the claim check and the
  routing note (exact build, the manager's share, VRAM).

Unreal jobs are reconciled after a worker restart, may be released by
their worker, accept a repeated claim by their assignee, and restrict
worker PATCHes to ``UNREAL_WORKER_PATCH_FIELDS``.  Tiled jobs and
animations are not supported yet (FR-MR-4).
"""

from ..constants import AssetSourceType, Renderer
from .base import RendererStrategy
from .unreal_routing import unreal_claim_rejection, unreal_poll_q, unreal_routing_note
from .unreal_settings import prepare_unreal_job, validate_unreal_job
from .unreal_validation import validate_unreal_asset, validate_unreal_project

# FR-RT-14's Unreal worker PATCH allowlist, applied on top of (never
# instead of) ``WORKER_PATCH_FIELDS`` (#217), which bounds every worker
# PATCH.  v4's set also listed ``started_at``; v4.1 drops it because #217
# does not allow it (the worker never sends it).  A unit test keeps this
# a subset of ``WORKER_PATCH_FIELDS``.
UNREAL_WORKER_PATCH_FIELDS = frozenset({
    'status', 'last_output', 'error_message', 'completed_at',
    'render_time_seconds',
})


class UnrealStrategy(RendererStrategy):
    """Rules for Unreal projects, shared-project assets and Unreal jobs."""

    renderer = Renderer.UNREAL
    source_type = AssetSourceType.SHARED_PROJECT
    label = 'Unreal'

    def validate_project(self, attrs, instance):
        return validate_unreal_project(attrs, instance)

    def prepare_project_create(self, validated):
        validated['blender_version'] = None
        return validated

    def validate_asset(self, attrs, instance):
        return validate_unreal_asset(attrs, instance)

    def validate_job(self, attrs, asset, instance):
        return validate_unreal_job(attrs, instance)

    def prepare_job_create(self, validated, asset):
        return prepare_unreal_job(validated, asset)

    def poll_q(self, params, worker=None):
        return unreal_poll_q(params, worker)

    def claim_rejection(self, worker, job):
        return unreal_claim_rejection(worker, job)

    def routing_note(self, job, context):
        return unreal_routing_note(job, context)

    def describe_asset(self, asset):
        return f"Archive: {asset.archive_path} ({asset.uproject_path})"

    def supports(self, feature):
        return False

    def idempotent_reclaim(self):
        return True

    def worker_patch_allowed_fields(self):
        return UNREAL_WORKER_PATCH_FIELDS

    def requeue_on_worker_restart(self):
        return True

    def supports_release(self):
        return True
