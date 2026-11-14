# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Renderer-generic serializer checks (Unreal foundation spec FR-MR-11).

``check_immutable_and_consistent`` runs in the ``validate()`` of the
Project, Asset and Job serializers **before** strategy dispatch, so a
strategy never sees an update that would change the renderer it was
chosen for.  ``resolve_strategy`` / ``resolve_source_strategy`` are the
single registry lookups, translating ``UnsupportedRendererError`` into
HTTP 400 (FR-MR-7).  Nothing here branches on a renderer value.
"""

from rest_framework import serializers

from ..models import Asset, Job, Project
from ..renderers import UnsupportedRendererError, get_strategy, strategy_for_source_type

# Asset fields that pin what a worker downloads; immutable once a job uses them.
DELIVERY_FIELDS = (
    'archive_path', 'uproject_path', 'archive_sha256',
    'overlay_path', 'overlay_sha256',
)


def resolve_strategy(renderer):
    """Return the strategy for *renderer*, or raise a 400 on ``renderer``."""
    try:
        return get_strategy(renderer)
    except UnsupportedRendererError:
        raise serializers.ValidationError(
            {'renderer': f"Unsupported renderer '{renderer}'."},
        ) from None


def resolve_source_strategy(source_type):
    """Return the strategy for an asset *source_type*, or raise a 400."""
    try:
        return strategy_for_source_type(source_type)
    except UnsupportedRendererError:
        raise serializers.ValidationError(
            {'source_type': f"Unsupported source type '{source_type}'."},
        ) from None


def _changed(attrs, instance, field):
    return field in attrs and attrs[field] != getattr(instance, field)


def _project_errors(instance, attrs):
    if _changed(attrs, instance, 'renderer'):
        return {'renderer': ["Renderer cannot be changed after creation."]}
    return {}


def _asset_errors(instance, attrs):
    errors = {}
    if _changed(attrs, instance, 'source_type'):
        errors['source_type'] = ["Source type cannot be changed after creation."]
    project = attrs.get('project')
    if project is not None:
        implied = resolve_source_strategy(instance.source_type).renderer
        if project.renderer != implied:
            errors['project'] = [
                "Cannot move an asset to a project of a different renderer."
            ]
    changed = [f for f in DELIVERY_FIELDS if _changed(attrs, instance, f)]
    if changed and instance.jobs.exists():
        for field in changed:
            errors[field] = [f"Cannot change {field} while jobs reference this asset."]
    return errors


def _job_errors(instance, attrs):
    errors = {}
    if _changed(attrs, instance, 'engine_build'):
        errors['engine_build'] = ["Engine build cannot be changed after creation."]
    asset = attrs.get('asset')
    if asset is not None and asset.project.renderer != instance.renderer:
        errors['asset_id'] = [
            "Cannot move a job to an asset of a different renderer."
        ]
    return errors


_CHECKS = (
    (Project, _project_errors),
    (Asset, _asset_errors),
    (Job, _job_errors),
)


def check_immutable_and_consistent(instance, attrs):
    """Reject updates that change a renderer-defining value (HTTP 400).

    * Project ``renderer`` and Asset ``source_type`` never change.
    * ``Job.engine_build`` is create-only.
    * A job cannot move to an asset of another renderer, nor an asset to
      a project of another renderer.  Same-renderer moves stay allowed.
    * Asset delivery fields are frozen once any job references the asset.

    A create (``instance is None``) is not checked here.

    Raises:
        rest_framework.exceptions.ValidationError: keyed by field.
    """
    if instance is None:
        return
    for model, check in _CHECKS:
        if isinstance(instance, model):
            errors = check(instance, attrs)
            if errors:
                raise serializers.ValidationError(errors)
            return
