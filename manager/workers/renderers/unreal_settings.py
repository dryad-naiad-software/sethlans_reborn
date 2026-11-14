# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal job rules: the strict ``render_settings`` schema and the phase-1
still rules (Unreal foundation FR-MU-10, FR-MU-11, FR-MU-12).

Cross-field rules are checked on the *effective* values
(``{**instance values, **attrs}``) whenever a participating key is sent,
so a PATCH cannot leave an Unreal job in a state the create would refuse.
A worker status PATCH sends none of these keys and is never refused here.
"""

import os

from rest_framework.exceptions import ValidationError

from shared.unreal.builds import is_supported
from shared.unreal.validation import validate_object_path

from ..constants import (
    FORMAT_EXTENSIONS, PILOT_NAME_PREFIX, UNREAL_OUTPUT_FORMATS, CyclesFeatureSet,
    RenderDevice, RenderEngine,
)

MAP = 'unreal.map'
SEQUENCE = 'unreal.sequence'
PRESET = 'unreal.preset'
OUTPUT_FORMAT = 'unreal.output_format'
REQUIRED_KEYS = (MAP, SEQUENCE, PRESET)
ALLOWED_KEYS = frozenset(REQUIRED_KEYS + (OUTPUT_FORMAT,))
DEFAULT_OUTPUT_FORMAT = 'PNG'

FRAME_MIN = -10_000_000
FRAME_MAX = 10_000_000

PNG_ONLY_MESSAGE = "Unreal jobs support PNG output only in this version."
# Model defaults of the Blender-only job fields: sending them unchanged is allowed.
_BLENDER_DEFAULTS = (
    ('render_engine', RenderEngine.CYCLES),
    ('cycles_feature_set', CyclesFeatureSet.SUPPORTED),
)


def build_message(value):
    """The FR-MU-8 message for an unusable engine build."""
    return f"Unreal Engine 5.8.x builds only (got '{value}')."


def check_engine_build(value, errors):
    """Add the FR-MU-8 error to ``errors`` unless ``value`` is a supported build."""
    if not is_supported(value):
        errors['engine_build'] = [build_message(value)]


def render_settings_errors(value):
    """Return the list of problems with Unreal ``render_settings`` (FR-MU-10)."""
    if not isinstance(value, dict):
        return ["Unreal render settings must be an object."]
    problems = []
    unknown = sorted(set(value) - ALLOWED_KEYS)
    if unknown:
        problems.append(f"Unknown Unreal render settings: {', '.join(unknown)}.")
    for key in REQUIRED_KEYS:
        if key not in value:
            problems.append(f"'{key}' is required.")
            continue
        try:
            validate_object_path(value[key])
        except ValueError as exc:
            problems.append(f"'{key}': {exc}")
    if OUTPUT_FORMAT in value and value[OUTPUT_FORMAT] not in UNREAL_OUTPUT_FORMATS:
        problems.append(PNG_ONLY_MESSAGE)
    return problems


def _effective(attrs, instance, field, default):
    if field in attrs:
        return attrs[field]
    return getattr(instance, field) if instance is not None else default


def _frame_errors(attrs, instance, errors):
    start = _effective(attrs, instance, 'start_frame', 1)
    end = _effective(attrs, instance, 'end_frame', 1)
    for field, frame in (('start_frame', start), ('end_frame', end)):
        if not FRAME_MIN <= frame <= FRAME_MAX:
            errors[field] = [f"Must be between {FRAME_MIN} and {FRAME_MAX}."]
    if not {'start_frame', 'end_frame'} & errors.keys() and start != end:
        errors['end_frame'] = [
            "Unreal jobs render a single frame: end_frame must equal start_frame."
        ]


def _not_applicable_errors(attrs, errors):
    if 'render_device' in attrs and attrs['render_device'] != RenderDevice.GPU:
        errors['render_device'] = ["Unreal jobs render on the GPU only."]
    for field, default in _BLENDER_DEFAULTS:
        if field in attrs and attrs[field] != default:
            errors[field] = [f"{field} is not applicable to Unreal jobs."]
    for field in ('blender_version', 'animation'):
        if attrs.get(field) is not None:
            errors[field] = ["Not applicable to Unreal jobs."]


def _output_pattern_errors(attrs, instance, errors):
    pattern = _effective(attrs, instance, 'output_file_pattern', '')
    expected = FORMAT_EXTENSIONS[DEFAULT_OUTPUT_FORMAT]
    if os.path.splitext(pattern or '')[1].lower() != expected:
        errors['output_file_pattern'] = [
            f"Unreal jobs write PNG files: the pattern must end with '{expected}'."
        ]


def _reserved_name_errors(attrs, instance, errors):
    """FR-PM-9: ``sethlans-pilot-`` names belong to manager-created pilots."""
    name = attrs.get('name')
    if not isinstance(name, str) or (instance is not None and name == instance.name):
        return
    if name.lower().startswith(PILOT_NAME_PREFIX):
        errors['name'] = [f'Names starting with "{PILOT_NAME_PREFIX}" are reserved for pilot jobs.']


def validate_unreal_job(attrs, instance):
    """Validate Unreal job ``attrs`` (``instance`` on update); return attrs.

    Raises:
        rest_framework.exceptions.ValidationError: keyed by field (HTTP 400).
    """
    creating = instance is None
    errors = {}
    if creating or {'start_frame', 'end_frame'} & attrs.keys():
        _frame_errors(attrs, instance, errors)
    _not_applicable_errors(attrs, errors)
    _reserved_name_errors(attrs, instance, errors)
    if attrs.get('engine_build'):
        check_engine_build(attrs['engine_build'], errors)
    if creating or 'render_settings' in attrs:
        problems = render_settings_errors(_effective(attrs, instance, 'render_settings', {}))
        if problems:
            errors['render_settings'] = problems
    if creating or {'output_file_pattern', 'render_settings'} & attrs.keys():
        _output_pattern_errors(attrs, instance, errors)
    if errors:
        raise ValidationError(errors)
    return attrs


def prepare_unreal_job(validated, asset):
    """Fill create-time defaults: GPU device, the pinned build, PNG output (FR-MU-3)."""
    validated.setdefault('render_device', RenderDevice.GPU)
    if not validated.get('engine_build'):
        validated['engine_build'] = asset.project.engine_build
    settings = dict(validated.get('render_settings') or {})
    settings.setdefault(OUTPUT_FORMAT, DEFAULT_OUTPUT_FORMAT)
    validated['render_settings'] = settings
    return validated
