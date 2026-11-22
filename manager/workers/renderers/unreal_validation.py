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

import re

from rest_framework.exceptions import ValidationError

from shared.unreal.share_paths import resolve_in_share, validate_share_relpath
from shared.unreal.validation import validate_cvars, validate_object_path, validate_sha256, validate_uproject_relpath
from shared.unreal.vram_gate import POOL_CVARS

from ..constants import Renderer
from ..constants_sequence import MAX_SOUND_OFFSETS, SOUND_OFFSET_MS_MAX
from ..services.sethlans_share import configured_share_root
from .unreal_settings import check_engine_build

SHARE_UNSET_ASSET_MESSAGE = (
    "Set the Sethlans share (PUT /api/sethlans-share/) before adding Unreal projects."
)
REQUIRED_MESSAGE = "This field is required."


def check_pool_cvars(cvars):
    """The pool CVars (``vram_gate.POOL_CVARS``), when set, are non-negative integer MB (FR-UE-21).

    The rule the removed pool-caps gate applied (per-machine memory test
    D7), kept as project validation: the worker fails a job whose pool
    value UE changed at render time (``cvars.check_effective``), so only a
    plain size is accepted.  ``cvars`` is already normalized by
    ``validate_cvars`` (string values, no name twice in any letter case).

    Raises:
        ValueError: naming the CVar.
    """
    pools = {name.casefold(): name for name in POOL_CVARS}
    for key, value in cvars.items():
        name = pools.get(key.casefold())
        if name is None:
            continue
        try:
            megabytes = int(str(value).strip())
        except ValueError:
            raise ValueError(f"{name}: pool size {value!r} is not an integer number of MB") from None
        if megabytes < 0:
            raise ValueError(f"{name}: pool size must not be negative")


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
            check_pool_cvars(cvars)
        except ValueError as exc:
            errors['unreal_cvar_overrides'] = [str(exc)]
        else:
            attrs['unreal_cvar_overrides'] = cvars
    if errors:
        raise ValidationError(errors)
    return attrs


_OFFSET_RATE = re.compile(r'([0-9]{1,7})/([0-9]{1,4})', re.ASCII)


def validate_sound_offsets(value):
    """D18 ``unreal_sound_offsets``: ``{"<preset object path>@<num>/<den>": ms}``, at most 64,
    each an integer of -10,000 to 10,000 ms; returns it or raises ``ValueError`` naming the entry."""
    if not isinstance(value, dict):
        raise ValueError("Must be an object.")
    if len(value) > MAX_SOUND_OFFSETS:
        raise ValueError(f"At most {MAX_SOUND_OFFSETS} entries.")
    for key, ms in value.items():
        preset, _at, rate = str(key).rpartition('@')
        match = _OFFSET_RATE.fullmatch(rate)
        if not preset or match is None or not (1 <= int(match[1]) <= 1_000_000 and 1 <= int(match[2]) <= 1_001):
            raise ValueError(f"'{key}': keys are \"<preset object path>@<num>/<den>\".")
        validate_object_path(preset)
        if isinstance(ms, bool) or not isinstance(ms, int) or abs(ms) > SOUND_OFFSET_MS_MAX:
            raise ValueError(f"'{key}': an integer of -{SOUND_OFFSET_MS_MAX} to {SOUND_OFFSET_MS_MAX} ms.")
    return value


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
