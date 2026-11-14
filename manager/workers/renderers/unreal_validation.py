# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal project and shared-project asset rules (Unreal foundation FR-MU-8,
FR-MU-9, FR-MU-12, FR-MU-15).

Archive and overlay locations are stored **relative to the manager's
Sethlans share** in normalized ``/`` form and checked by resolving them
under the configured root (``resolve_in_share``): the root plus a
validated relative path, never a full path supplied by the client. The
manager does not stat the share; the worker checks existence at job time.
"""

from rest_framework.exceptions import ValidationError

from shared.unreal.share_paths import resolve_in_share, validate_share_relpath
from shared.unreal.validation import validate_cvars, validate_sha256, validate_uproject_relpath
from shared.unreal.vram_gate import vram_need_mb

from ..constants import Renderer
from ..services.sethlans_share import configured_share_root
from .unreal_settings import check_engine_build

SHARE_UNSET_ASSET_MESSAGE = (
    "Set the Sethlans share (PUT /api/sethlans-share/) before adding Unreal projects."
)
REQUIRED_MESSAGE = "This field is required."


def validate_unreal_project(attrs, instance):
    """FR-MU-8 on the effective values; returns attrs with CVars normalized.

    Raises:
        rest_framework.exceptions.ValidationError: keyed by field.
    """
    errors = {}
    if instance is None or 'engine_build' in attrs:
        check_engine_build(attrs.get('engine_build', ''), errors)
    if attrs.get('blender_version') is not None:
        errors['blender_version'] = ["Not applicable to Unreal projects."]
    if 'unreal_cvar_overrides' in attrs:
        try:
            cvars = validate_cvars(attrs['unreal_cvar_overrides'])
            # The worker's VRAM gate reads the pool CVars as integers (FR-UE-21).
            vram_need_mb(cvars)
        except ValueError as exc:
            errors['unreal_cvar_overrides'] = [str(exc)]
        else:
            attrs['unreal_cvar_overrides'] = cvars
    if errors:
        raise ValidationError(errors)
    return attrs


def _share_relpath(root, value, **rules):
    relpath = validate_share_relpath(value, **rules)
    resolve_in_share(root, relpath)
    return relpath


def _check_field(attrs, errors, field, check):
    """Replace ``attrs[field]`` by ``check(value)``; a ``ValueError`` becomes an error."""
    value = attrs.get(field, '')
    if value == '':
        errors[field] = [REQUIRED_MESSAGE]
        return
    try:
        attrs[field] = check(value)
    except ValueError as exc:
        errors[field] = [str(exc)]


def _effective(attrs, instance, field):
    if field in attrs:
        return attrs[field]
    return getattr(instance, field) if instance is not None else ''


def _overlay_errors(attrs, instance, root, errors):
    if attrs.get('overlay_path'):
        _check_field(attrs, errors, 'overlay_path',
                     lambda v: _share_relpath(root, v, kind='dir'))
    if not ({'overlay_path', 'overlay_sha256'} & attrs.keys()):
        return
    has_path = bool(_effective(attrs, instance, 'overlay_path'))
    has_pin = bool(_effective(attrs, instance, 'overlay_sha256'))
    if has_path and not has_pin:
        errors['overlay_sha256'] = ["Required when overlay_path is set."]
    elif has_pin and not has_path:
        errors['overlay_sha256'] = ["Not allowed without overlay_path."]
    elif attrs.get('overlay_sha256'):
        _check_field(attrs, errors, 'overlay_sha256', validate_sha256)


def validate_unreal_asset(attrs, instance):
    """FR-MU-9 on the effective values; returns attrs with paths and pins normalized.

    Raises:
        rest_framework.exceptions.ValidationError: keyed by field.
    """
    creating = instance is None
    project = attrs.get('project') or instance.project
    if project.renderer != Renderer.UNREAL:
        raise ValidationError({'source_type': ["Shared-project assets require an Unreal project."]})
    root = None
    if creating or {'archive_path', 'overlay_path'} & attrs.keys():
        root = configured_share_root()
        if root is None:
            raise ValidationError({'archive_path': [SHARE_UNSET_ASSET_MESSAGE]})
    errors = {}
    if attrs.get('blend_file'):
        errors['blend_file'] = ["Not applicable to shared-project assets."]
    if creating or 'archive_path' in attrs:
        _check_field(attrs, errors, 'archive_path',
                     lambda v: _share_relpath(root, v, kind='file', suffix='.zip'))
    if creating or 'uproject_path' in attrs:
        _check_field(attrs, errors, 'uproject_path', validate_uproject_relpath)
    if creating or 'archive_sha256' in attrs:
        _check_field(attrs, errors, 'archive_sha256', validate_sha256)
    _overlay_errors(attrs, instance, root, errors)
    if errors:
        raise ValidationError(errors)
    return attrs
