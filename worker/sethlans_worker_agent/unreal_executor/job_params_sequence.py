# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Unreal job kinds and render settings of sequence rendering in a poll
payload (sequence rendering FR-SW-1, FR-SW-2, FR-SD-3, D3, D8, D11), kept
apart so ``job_params`` stays small. ``job_params`` passes its own
``require(condition, field, reason)``.

* ``shots`` (shot discovery): ``start_frame == end_frame == 0`` and no
  measured peak; the job renders nothing (FR-SD-4).
* a **piece** is a ``render`` job with a ``sequence_submission``: kept
  frames ``start_frame..end_frame`` (inclusive, ``0 <= start <= end``), an
  int ``unreal_lead_in_frames`` in ``[0, start_frame]``, at most 200,000
  rendered frames, PNG or EXR, and ``unreal.piece_shots``: 1-2,000
  ``[index, start, end]`` rows (end exclusive) that cover the kept frames
  in order without a gap, indexes increasing.
* ``audio`` (a sound run, FR-SA-1): a ``sequence_submission``, the run
  ``start_frame..end_frame`` and its audio lead-in as a piece has them, no
  measured peak, PNG (no other format applies), and the run's shot parts
  as ``unreal.piece_shots`` (``shared.unreal.sound_result.RUN_SHOTS_KEY``);
  no other manager-only key.
* The manager-only keys a job may carry (FR-SW-2): a piece
  ``unreal.resolution``, ``unreal.shot_set_sha256``, ``unreal.piece_shots``,
  ``unreal.output_size`` and ``unreal.draft``; a pilot ``unreal.resolution``,
  ``unreal.shot_set_sha256`` and ``unreal.pilot_frames`` (1-256 increasing
  ints >= 0); a standalone render ``unreal.resolution`` only. Anything else
  is refused naming the key.

Bounds: ``unreal.resolution`` ints 64-16,384; ``unreal.output_size`` ints
1-16,384; ``unreal.shot_set_sha256`` 64 lowercase hex; ``unreal.draft``
only the FR-SQ-7 keys (samples ints 1-1,024; ``screen_percentage`` 10-100;
the three switches only ``false``). The draft's CVars come only from
``cvars.DRAFT_CVARS`` (D11).
"""
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

from sethlans_worker_agent.unreal_executor.cvars import DRAFT_CVARS

SHOTS, AUDIO = 'shots', 'audio'
KINDS = (SHOTS, AUDIO)
RESOLUTION, SHOT_SET, PIECE_SHOTS = 'unreal.resolution', 'unreal.shot_set_sha256', 'unreal.piece_shots'
OUTPUT_SIZE, PILOT_FRAMES, DRAFT = 'unreal.output_size', 'unreal.pilot_frames', 'unreal.draft'
EXTRA_KEYS = frozenset({RESOLUTION, SHOT_SET, PIECE_SHOTS, OUTPUT_SIZE, PILOT_FRAMES, DRAFT})
_ALLOWED = {'piece': {RESOLUTION, SHOT_SET, PIECE_SHOTS, OUTPUT_SIZE, DRAFT},
            'pilot': {RESOLUTION, SHOT_SET, PILOT_FRAMES}, 'render': {RESOLUTION}, SHOTS: set(), AUDIO: {PIECE_SHOTS}}
FRAME_MAX = 10_000_000
MAX_SHOTS, MAX_PILOT_FRAMES, MAX_PIECE_FRAMES = 2_000, 256, 200_000
RES_MIN, SIZE_MAX, SAMPLES_MAX = 64, 16_384, 1_024
DRAFT_SAMPLES = ('temporal_samples', 'spatial_samples')
DRAFT_SWITCHES = ('motion_blur', 'ray_traced_shadows', 'ray_traced_reflections')
DRAFT_KEYS = DRAFT_SAMPLES + ('screen_percentage',) + DRAFT_SWITCHES
_SHA = re.compile(r'[0-9a-f]{64}', re.ASCII)
Require = Callable[[bool, str, str], None]


@dataclass(frozen=True)
class Piece:
    """A sequence piece: kept frames ``start..end`` (inclusive) after ``lead`` discarded lead-in frames."""
    submission: int
    start: int
    end: int
    lead: int
    shots: Tuple[Tuple[int, int, int], ...]   # (index, start, end), end exclusive, covering start..end
    output_size: Optional[Tuple[int, int]] = None

    @property
    def first(self) -> int:
        """The first rendered frame (the lead-in's first frame, else ``start``)."""
        return self.start - self.lead

    @property
    def rendered(self) -> int:
        return self.end - self.start + 1 + self.lead


@dataclass(frozen=True)
class SequenceSettings:
    resolution: Optional[Tuple[int, int]] = None
    shot_set_sha256: str = ''
    pilot_frames: Tuple[int, ...] = ()
    draft: Tuple[Tuple[str, Any], ...] = ()
    piece: Optional[Piece] = None
    audio: Optional[Piece] = None    # an audio job's run: its frames, audio lead-in and shot parts (FR-SA-1)

    @property
    def draft_cvars(self) -> Tuple[Tuple[str, str], ...]:
        """The draft tier (D11): ``(name, value)`` from the fixed ``DRAFT_CVARS`` only."""
        values = dict(self.draft)
        return tuple((name, str(values[key]) if key == 'screen_percentage' else '0')
                     for key, name in DRAFT_CVARS.items() if key in values)


NONE = SequenceSettings()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def check(kind: str, job_data: dict, require: Require) -> Optional[int]:
    """FR-SD-3, D3: no measurement key holds a ``shots`` or ``audio`` job; a ``shots`` job carries frame 0 only."""
    require(job_data.get('unreal_measured_peak_mb') is None, 'unreal_measured_peak_mb',
            f"must be null for a {kind} job")
    start, end = job_data.get('start_frame'), job_data.get('end_frame')
    require(kind != SHOTS or (_is_int(start) and _is_int(end) and start == end == 0), 'start_frame',
            f"a {kind} job has start_frame == end_frame == 0")
    return None


def submission_of(job_data: dict, require: Require) -> Optional[int]:
    """The piece's ``sequence_submission`` id, or ``None`` for any other job."""
    value = job_data.get('sequence_submission')
    require(value is None or (_is_int(value) and value > 0), 'sequence_submission', 'must be null or an id > 0')
    return value


def _size(raw: dict, key: str, low: int, require: Require) -> Optional[Tuple[int, int]]:
    value = raw.get(key)
    if value is None:
        return None
    require(isinstance(value, list) and len(value) == 2 and all(_is_int(v) and low <= v <= SIZE_MAX for v in value),
            f'render_settings.{key}', f"must be [w, h], ints {low}-{SIZE_MAX}")
    return value[0], value[1]


def _pilot_frames(raw: dict, require: Require) -> Tuple[int, ...]:
    value = raw.get(PILOT_FRAMES)
    if value is None:
        return ()
    ok = isinstance(value, list) and 1 <= len(value) <= MAX_PILOT_FRAMES \
        and all(_is_int(f) and 0 <= f <= FRAME_MAX for f in value) and all(a < b for a, b in zip(value, value[1:]))
    require(ok, f'render_settings.{PILOT_FRAMES}', f"must be 1-{MAX_PILOT_FRAMES} increasing frames >= 0")
    return tuple(value)


def _draft_value(key: str, value: Any) -> bool:
    if key in DRAFT_SAMPLES:
        return _is_int(value) and 1 <= value <= SAMPLES_MAX
    if key == 'screen_percentage':
        return _is_int(value) and 10 <= value <= 100
    return value is False


def _draft(raw: dict, require: Require) -> Tuple[Tuple[str, Any], ...]:
    value = raw.get(DRAFT)
    if value is None:
        return ()
    require(isinstance(value, dict), f'render_settings.{DRAFT}', 'must be an object')
    for key in sorted(value, key=str):
        require(key in DRAFT_KEYS and _draft_value(key, value[key]), f'render_settings.{DRAFT}.{key}',
                'not a draft key, or out of range')
    return tuple(sorted(value.items()))


def _piece_shots(raw: dict, start: int, end: int, require: Require) -> Tuple[Tuple[int, int, int], ...]:
    field, value = f'render_settings.{PIECE_SHOTS}', raw.get(PIECE_SHOTS)
    require(isinstance(value, list) and 1 <= len(value) <= MAX_SHOTS, field, f"1-{MAX_SHOTS} [index, start, end] rows")
    rows, at, last = [], start, 0
    for row in value:
        require(isinstance(row, list) and len(row) == 3 and all(_is_int(v) for v in row), field,
                "rows are [index, start, end] ints")
        index, lo, hi = row
        require(last < index <= MAX_SHOTS and lo == at and lo < hi, field,
                "rows must cover the kept frames in order, without a gap, indexes increasing")
        rows.append((index, lo, hi))
        at, last = hi, index
    require(at == end + 1, field, "rows must end at end_frame + 1")
    return tuple(rows)


def _piece(job_data: dict, raw: dict, submission: int, require: Require) -> Piece:
    start, end, lead = job_data.get('start_frame'), job_data.get('end_frame'), job_data.get('unreal_lead_in_frames')
    require(_is_int(start) and _is_int(end) and 0 <= start <= end <= FRAME_MAX, 'end_frame',
            "a piece needs 0 <= start_frame <= end_frame")
    require(_is_int(lead) and 0 <= lead <= start, 'unreal_lead_in_frames', "must be an int in [0, start_frame]")
    require(end - start + 1 + lead <= MAX_PIECE_FRAMES, 'end_frame', f"a piece renders at most {MAX_PIECE_FRAMES} frames")
    return Piece(submission=submission, start=start, end=end, lead=lead, shots=_piece_shots(raw, start, end, require),
                 output_size=_size(raw, OUTPUT_SIZE, 1, require))


def settings(kind: str, job_data: dict, raw: dict, output_format: str, require: Require) -> SequenceSettings:
    """FR-SW-1, FR-SW-2: the sequence settings of a job whose base settings ``job_params`` checked."""
    submission = submission_of(job_data, require) if kind in ('render', AUDIO) else None
    role = 'piece' if kind == 'render' and submission is not None else kind
    require(role != AUDIO or submission is not None, 'sequence_submission', "an audio job belongs to a submission")
    refused = sorted((set(raw) & EXTRA_KEYS) - _ALLOWED.get(role, set()))
    require(not refused, 'render_settings', f"{refused} are set by sequence renders only")
    require(output_format == 'PNG' or (role == 'piece' and output_format == 'EXR'),
            'render_settings.unreal.output_format', 'PNG, or EXR for a sequence piece')
    if role not in ('piece', AUDIO):
        require(job_data.get('unreal_lead_in_frames') in (None, 0), 'unreal_lead_in_frames', "only a piece has one")
    sha = raw.get(SHOT_SET, '')
    require(sha == '' or (isinstance(sha, str) and _SHA.fullmatch(sha) is not None), f'render_settings.{SHOT_SET}',
            "must be 64 lowercase hex characters")
    return SequenceSettings(resolution=_size(raw, RESOLUTION, RES_MIN, require), shot_set_sha256=sha,
                            pilot_frames=_pilot_frames(raw, require), draft=_draft(raw, require),
                            piece=_piece(job_data, raw, submission, require) if role == 'piece' else None,
                            audio=_piece(job_data, raw, submission, require) if role == AUDIO else None)
