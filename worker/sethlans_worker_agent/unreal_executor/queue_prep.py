# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Per-job queue prep: the params file and the result check (Unreal
foundation FR-UE-8, FR-UE-7 "prep-result return path", FR-UE-25 prep).

``write_params`` writes ``params.json`` (atomic replace) from validated
``JobParams`` only; no job value is ever interpolated into Python source
(UE scripts read this file through ``SETHLANS_UE_JOB_PARAMS``).

``read_result`` reads the prep result, which a UE process running project
code wrote, so it is **untrusted** (Sec M4):

* ``queue_asset`` matches ``OBJECT_PATH_RE`` and starts with
  ``package_root + "/"``; ``preset_kind`` is ``graph`` or ``legacy``;
  ``playback_range`` is two ints; ``camera_cut_starts`` ints;
* ``error`` and every ``warnings[]`` string is truncated to 2 KiB with
  control characters stripped;
* a pilot's ``pilot_frames`` are ints inside ``playback_range``, strictly
  increasing, 1-256 entries and as many as the queue's jobs.

A violation raises ``PrepError("Prep pass returned an invalid result
(<field>).")``; ``ok: false`` raises ``PrepError`` with the sanitized error.
"""
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from shared.unreal import validation

SCHEMA = 1
PACKAGE_ROOT = '/Game/_SethlansFarm/Job{job_id}'
FILE_NAME_FORMAT = 'sethlans_{job_id}.{{frame_number}}'
MAX_RESULT_BYTES = 1024 * 1024
MAX_TEXT = 2048
MAX_LIST = 4096
PILOT_MAX_FRAMES = 256
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')
_OBJECT_PATH = re.compile(validation.OBJECT_PATH_RE, re.ASCII)


class PrepError(Exception):
    """The prep pass failed or returned an invalid result; ``str`` is the job's error message."""


def invalid(field_name: str) -> PrepError:
    return PrepError(f"Prep pass returned an invalid result ({field_name}).")


def sanitize(text, limit: int = MAX_TEXT) -> str:
    return _CONTROL.sub('', str(text))[:limit]


def package_root(job_id: int) -> str:
    return PACKAGE_ROOT.format(job_id=int(job_id))


def file_name_format(job_id: int) -> str:
    return FILE_NAME_FORMAT.format(job_id=int(job_id))


def write_json(path, data: dict) -> None:
    """Atomic replace of a small JSON file."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex[:8]}")
    try:
        with open(tmp, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, indent=1)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def prep_params(params, output_dir: str, result_path: str) -> Dict[str, Any]:
    data = {'schema': SCHEMA, 'job_id': params.job_id,
            'mode': 'prepare_pilot' if params.is_pilot else 'prepare',
            'map': params.map, 'sequence': params.sequence, 'preset': params.preset,
            'output_format': params.output_format, 'output_dir': str(output_dir),
            'file_name_format': file_name_format(params.job_id), 'package_root': package_root(params.job_id),
            'result_path': str(result_path), 'cvar_names': [name for name, _value in params.merged],
            'max_pilot_frames': PILOT_MAX_FRAMES}
    if not params.is_pilot:
        data['frame'] = params.frame
    return data


@dataclass(frozen=True)
class PrepResult:
    queue_asset: str
    preset_kind: str
    playback_range: Tuple[int, int]
    camera_cut_starts: Tuple[int, ...]
    effective_cvars: Dict[str, Any]
    warnings: Tuple[str, ...] = ()
    pilot_frames: Tuple[int, ...] = ()
    queue_jobs: int = 1
    scalability: int = 4
    extra: Dict[str, Any] = field(default_factory=dict)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def load(path) -> Optional[dict]:
    """The parsed result, or None when missing, oversized or unparseable."""
    try:
        if os.stat(path).st_size > MAX_RESULT_BYTES:
            return None
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def result_ok(path) -> bool:
    """The prep phase's success marker: the result file parses with ``ok: true``."""
    data = load(path)
    return bool(data and data.get('ok') is True)


def _ints(data: dict, key: str, required: bool = True) -> Tuple[int, ...]:
    raw = data.get(key, None if required else [])
    if not isinstance(raw, list) or len(raw) > MAX_LIST or not all(_is_int(item) for item in raw):
        raise invalid(key)
    return tuple(raw)


def _check_pilot(data: dict, playback: Tuple[int, int], sequence: str) -> Tuple[Tuple[int, ...], int]:
    raw = data.get('pilot_frames')
    if isinstance(raw, list) and all(_is_int(f) for f in raw) and any(f < 0 for f in raw):
        raise PrepError("Pilot render failed: sequences with shots before frame 0 are not supported in phase 1.")
    frames = _ints(data, 'pilot_frames')
    if not frames:
        raise PrepError(f"No camera cut found in {sequence}; cannot measure.")
    if len(frames) > PILOT_MAX_FRAMES:
        raise PrepError(f"Sequence has {len(frames)} shots; the pilot supports at most {PILOT_MAX_FRAMES}.")
    jobs = data.get('queue_jobs')
    inside = all(playback[0] <= f < playback[1] for f in frames)
    increasing = all(a < b for a, b in zip(frames, frames[1:]))
    if not inside or not increasing or not _is_int(jobs) or jobs != len(frames):
        raise invalid('pilot_frames')
    return frames, jobs


def _warnings(data: dict) -> Tuple[str, ...]:
    raw = data.get('warnings', [])
    if not isinstance(raw, list):
        raise invalid('warnings')
    return tuple(sanitize(item) for item in raw[:64])


def parse_result(data: Optional[dict], root: str, pilot: bool, sequence: str) -> PrepResult:
    if data is None:
        raise PrepError("Prep pass wrote no readable result.")
    if data.get('ok') is not True:
        raise PrepError(sanitize(data.get('error') or 'Prep pass failed without an error message.'))
    queue = data.get('queue_asset')
    if not isinstance(queue, str) or len(queue) > validation.OBJECT_PATH_MAX or not _OBJECT_PATH.fullmatch(queue) \
            or not queue.startswith(root + '/'):
        raise invalid('queue_asset')
    if data.get('preset_kind') not in ('graph', 'legacy'):
        raise invalid('preset_kind')
    playback = data.get('playback_range')
    if not isinstance(playback, list) or len(playback) != 2 or not all(_is_int(v) for v in playback):
        raise invalid('playback_range')
    effective = data.get('effective_cvars', {})
    if not isinstance(effective, dict):
        raise invalid('effective_cvars')
    scalability = data.get('scalability', 4)
    if not _is_int(scalability) or not 0 <= scalability <= 4:
        raise invalid('scalability')
    frames, jobs = _check_pilot(data, tuple(playback), sequence) if pilot else ((), 1)
    return PrepResult(queue_asset=queue, preset_kind=data['preset_kind'], playback_range=tuple(playback),
                      camera_cut_starts=_ints(data, 'camera_cut_starts', required=False),
                      effective_cvars=effective, warnings=_warnings(data), pilot_frames=frames, queue_jobs=jobs,
                      scalability=scalability)


def read_result(path, params) -> PrepResult:
    return parse_result(load(path), package_root(params.job_id), params.is_pilot, params.sequence)


def warmup_params(params, prep: PrepResult, result_path: str, step_frames: int, step_seconds: int,
                  hold_seconds: int) -> Dict[str, Any]:
    frames: List[int] = list(prep.pilot_frames) if params.is_pilot else [params.frame]
    return {'schema': SCHEMA, 'job_id': params.job_id, 'mode': 'warmup', 'map': params.map,
            'sequence': params.sequence, 'frames': frames, 'camera_cut_starts': list(prep.camera_cut_starts),
            'step_frames': int(step_frames), 'step_seconds': int(step_seconds), 'hold_seconds': int(hold_seconds),
            'scalability': prep.scalability, 'result_path': str(result_path)}
