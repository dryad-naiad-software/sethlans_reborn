# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Validation of an Unreal job's poll payload (Unreal foundation FR-UE-1
check 2; Sec M4).

``validate_job_data(job_data)`` runs **before any share access** and
returns a frozen, typed ``JobParams``; the executor uses only that object
afterwards, never the raw dict. It raises ``JobDataError`` naming the
offending field. Checks:

* ``id`` is an ``int > 0``; ``renderer == 'UNREAL'``;
* ``asset.source_type == 'shared_project'``; ``asset.uproject_path`` passes
  FR-MU-7; ``archive_path``/``overlay_path`` pass FR-MU-15 and equal their
  normalized forms; ``archive_sha256`` (and ``overlay_sha256`` with an
  overlay) passes ``validate_sha256``;
* ``render_settings`` holds exactly the FR-MU-10 keys (map, sequence and
  preset required, output format optional) with valid object paths, and
  the output format is ``PNG`` (D5);
* ``start_frame == end_frame``, ints in range (normal jobs only: a pilot's
  frames come from the prep, FR-UE-25);
* ``engine_build`` matches ``BUILD_RE`` and is supported;
* ``unreal_kind`` is ``'render'`` (also when missing) or ``'pilot'``; a
  normal job needs an ``int`` ``unreal_measured_peak_mb`` in
  ``[1, 1048576]``, a pilot must have none (D2);
* ``asset.project_details.unreal_cvar_overrides`` passes ``validate_cvars``.

The merged CVar list (FR-UE-5) is computed here once, so every later use
(gate, command line, marker hash, pilot report) sees the same list.
"""
import dataclasses
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from sethlans_worker_agent.unreal_executor import cvars
from shared.unreal import builds, validation
from shared.unreal.share_paths import validate_share_relpath
from shared.unreal.unc import UncPath
from shared.unreal.vram_gate import MAX_PEAK_MB

MAP, SEQUENCE, PRESET, OUTPUT_FORMAT = 'unreal.map', 'unreal.sequence', 'unreal.preset', 'unreal.output_format'
REQUIRED_SETTINGS = (MAP, SEQUENCE, PRESET)
ALLOWED_SETTINGS = frozenset(REQUIRED_SETTINGS + (OUTPUT_FORMAT,))
FRAME_MIN, FRAME_MAX = -10_000_000, 10_000_000
KINDS = ('render', 'pilot')


class JobDataError(ValueError):
    """The poll payload of an Unreal job fails a FR-UE-1 check 2 rule."""

    def __init__(self, field: str, reason: str):
        self.field = field
        super().__init__(f"{field}: {reason}")


@dataclass(frozen=True)
class JobParams:
    job_id: int
    kind: str
    engine_build: str
    map: str
    sequence: str
    preset: str
    output_format: str
    frame: Optional[int]
    measured_peak_mb: Optional[int]
    archive_path: str
    archive_sha256: str
    overlay_path: str
    overlay_sha256: str
    uproject_path: str
    cvar_overrides: Tuple[Tuple[str, str], ...]
    merged: Tuple[Tuple[str, str], ...]
    share_root: Optional[UncPath] = None

    @property
    def is_pilot(self) -> bool:
        return self.kind == 'pilot'

    @property
    def merged_dict(self) -> Dict[str, str]:
        return dict(self.merged)

    def with_share_root(self, root: UncPath) -> 'JobParams':
        return dataclasses.replace(self, share_root=root)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require(condition: bool, field: str, reason: str) -> None:
    if not condition:
        raise JobDataError(field, reason)


def _checked(field: str, fn, value):
    try:
        return fn(value)
    except (ValueError, TypeError) as exc:
        raise JobDataError(field, str(exc)) from None


def _settings(raw) -> Tuple[str, str, str, str]:
    _require(isinstance(raw, dict), 'render_settings', 'must be an object')
    unknown = sorted(set(raw) - ALLOWED_SETTINGS)
    _require(not unknown, 'render_settings', f"unknown keys {unknown}")
    values = []
    for key in REQUIRED_SETTINGS:
        _require(key in raw, 'render_settings', f"'{key}' is required")
        values.append(_checked(f'render_settings.{key}', validation.validate_object_path, raw[key]))
    output = raw.get(OUTPUT_FORMAT, 'PNG')
    _require(output == 'PNG', 'render_settings.unreal.output_format', 'only PNG is supported (D5)')
    return values[0], values[1], values[2], output


def _relpath(field: str, value, kind: str, suffix=None) -> str:
    _require(isinstance(value, str), field, 'must be a string')
    normalized = _checked(field, lambda v: validate_share_relpath(v, kind=kind, suffix=suffix), value)
    _require(normalized == value, field, 'is not in normalized form')
    return value


def _asset(asset) -> Dict[str, str]:
    _require(isinstance(asset, dict), 'asset', 'must be an object')
    _require(asset.get('source_type') == 'shared_project', 'asset.source_type', "must be 'shared_project'")
    out = {'uproject_path': _checked('asset.uproject_path', validation.validate_uproject_relpath,
                                     asset.get('uproject_path')),
           'archive_path': _relpath('asset.archive_path', asset.get('archive_path'), 'file', '.zip'),
           'archive_sha256': _checked('asset.archive_sha256', validation.validate_sha256, asset.get('archive_sha256')),
           'overlay_path': '', 'overlay_sha256': ''}
    overlay = asset.get('overlay_path')
    if overlay not in (None, ''):
        out['overlay_path'] = _relpath('asset.overlay_path', overlay, 'dir')
        out['overlay_sha256'] = _checked('asset.overlay_sha256', validation.validate_sha256,
                                         asset.get('overlay_sha256'))
    return out


def _overrides(asset) -> Tuple[Tuple[str, str], ...]:
    project = asset.get('project_details')
    _require(isinstance(project, dict), 'asset.project_details', 'must be an object')
    raw = project.get('unreal_cvar_overrides')
    raw = {} if raw is None else raw
    checked = _checked('asset.project_details.unreal_cvar_overrides', validation.validate_cvars, raw)
    return tuple(checked.items())


def _frame(job_data) -> int:
    start, end = job_data.get('start_frame'), job_data.get('end_frame')
    _require(_is_int(start) and _is_int(end), 'start_frame', 'start and end frames must be integers')
    _require(start == end, 'end_frame', 'must equal start_frame (single frame)')
    _require(FRAME_MIN <= start <= FRAME_MAX, 'start_frame', 'out of range')
    return start


def _kind_and_peak(job_data) -> Tuple[str, Optional[int]]:
    kind = job_data.get('unreal_kind') or 'render'
    _require(kind in KINDS, 'unreal_kind', "must be 'render' or 'pilot'")
    peak = job_data.get('unreal_measured_peak_mb')
    if kind == 'pilot':
        _require(peak is None, 'unreal_measured_peak_mb', 'must be null for a pilot')
        return kind, None
    _require(_is_int(peak) and 1 <= peak <= MAX_PEAK_MB, 'unreal_measured_peak_mb',
             f'a normal job needs a measured peak in [1, {MAX_PEAK_MB}]')
    return kind, peak


def validate_job_data(job_data: dict) -> JobParams:
    """FR-UE-1 check 2; raises ``JobDataError``."""
    _require(isinstance(job_data, dict), 'job', 'must be an object')
    job_id = job_data.get('id')
    _require(_is_int(job_id) and job_id > 0, 'id', 'must be an integer > 0')
    _require(job_data.get('renderer') == 'UNREAL', 'renderer', "must be 'UNREAL'")
    asset_raw = job_data.get('asset')
    asset = _asset(asset_raw)
    map_path, sequence, preset, output = _settings(job_data.get('render_settings'))
    build = job_data.get('engine_build')
    _require(isinstance(build, str) and _checked('engine_build', builds.parse_build, build) is not None,
             'engine_build', 'invalid')
    _require(builds.is_supported(build), 'engine_build', 'not a supported build')
    kind, peak = _kind_and_peak(job_data)
    frame = None if kind == 'pilot' else _frame(job_data)
    overrides = _overrides(asset_raw)
    try:
        merged = tuple(cvars.merge(overrides=dict(overrides)))
    except ValueError as exc:
        raise JobDataError('asset.project_details.unreal_cvar_overrides', str(exc)) from None
    return JobParams(job_id=job_id, kind=kind, engine_build=build, map=map_path, sequence=sequence, preset=preset,
                     output_format=output, frame=frame, measured_peak_mb=peak, cvar_overrides=overrides,
                     merged=merged, **asset)
