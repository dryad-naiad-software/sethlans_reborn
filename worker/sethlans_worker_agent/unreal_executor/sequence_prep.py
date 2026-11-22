# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Prep params and strict result parsers for sequence rendering (FR-SD-4..7).

``shots_params`` writes the ``list_shots`` params (validated ``JobParams``
values only; no job value ever reaches Python source, NF-7).

``parse_shots`` checks the ``list_shots`` result, which a UE process running
project code wrote, so it is **untrusted** (Security notes). The rules are
``shared.unreal.shots_result``, the one home the manager checks the same
body with (FR-SD-8): every key required and no other, the FR-SD-7 bounds,
and the FR-SD-6 shot names cleaned again. Before that check the worker only
caps and cleans what it may shorten: warnings (64 strings of 512
characters, control characters stripped) and the two shown-only words
``game_overrides`` / ``texture_streaming`` (anything outside their
allowlists is ``unknown``, FR-GC-4). Any other fault raises
``JobFailure("Shot discovery returned an invalid result (<field>).")``; a
playback start below 0 raises ``JobFailure("Sequences that start before
frame 0 are not supported.")``. ``Discovery.body()`` is what the worker
sends to ``shots-result/``.

``range_params`` writes a piece's ``prepare_range`` params (FR-SW-3): the
rendered range ``[start - lead, end + 1)``, the first kept frame (it must
lie in a camera cut), the output format, the resolution, the draft and the
names of the draft CVars the job sends. ``draft_conflicts`` checks the
prep's untrusted ``draft_conflicts``: at most 16 names, each one of the
draft CVars this job sent (D11, Security notes); anything else is
``PrepError("Prep pass returned an invalid result (draft_conflicts).")``.

An audio job (FR-SA-2, FR-SA-9): ``audio_params`` writes the
``prepare_audio`` params (the range ``[r0, end + 1)`` and the run's first
frame); ``frame_rate`` checks the prep's ``frame_rate`` (the FR-SD-7
bounds). ``export_params`` writes the export commandlet's ``export_audio``
params; ``parse_sources`` checks its ``audio_sources.json``, which a UE
process running project code wrote, so it is **untrusted**: at most 64
sections; a row ``{"unsupported": reason}`` is only counted; any other row
has exactly ``start``, ``end`` (ints, overlapping ``[r0, end)``),
``start_offset_samples`` (an int >= 0, source samples), ``volume`` (0-4),
``looping`` (bool), ``wav`` (a plain ``src_<n>.wav`` name in the export
folder) and ``sample_rate`` (8,000-192,000). Anything else raises
``SourcesError`` naming the field (the measurement is then
``unavailable``; the job never fails on it).
"""
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from sethlans_worker_agent.unreal_executor import queue_prep
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure
from sethlans_worker_agent.unreal_executor.queue_prep import sent_draft_cvars  # re-exported: pieces use it here
from shared.unreal import shots_result, sound_result

GIB = 1024 ** 3
SCHEMA = queue_prep.SCHEMA
MAX_SHOTS, FRAME_MAX = shots_result.MAX_SHOTS, shots_result.FRAME_MAX
MAX_DRAFT_CONFLICTS = 16
MAX_SECTIONS, MAX_EXPORT_BYTES, MAX_OFFSET_SAMPLES = sound_result.MAX_SECTIONS, 4 * GIB, 2 ** 40
SOURCE_KEYS = ('start', 'end', 'start_offset_samples', 'volume', 'looping', 'wav', 'sample_rate')
_WAV_NAME = re.compile(r'src_[0-9]{3}\.wav', re.ASCII)
_WORDS = {'game_overrides': queue_prep.GAME_OVERRIDES, 'texture_streaming': queue_prep.TEXTURE_STREAMING}


def range_params(params, output_dir, result_path, scripts_dir) -> Dict[str, Any]:
    """FR-SW-3: a piece's prep params, from the validated ``JobParams`` only (NF-7)."""
    piece, seq = params.piece, params.seq
    data = queue_prep.prep_params(params, output_dir, result_path, scripts_dir)
    data.pop('frame', None)
    data.update(mode='prepare_range', range=[piece.first, piece.end + 1], start=piece.start, draft=dict(seq.draft),
                draft_cvars=[name for name, _value in sent_draft_cvars(params)])
    return data


def draft_conflicts(data: Optional[dict], sent) -> Tuple[str, ...]:
    """D11: the draft CVars the preset sets itself, as the names this job sent (``sent``: ``(name, value)``)."""
    raw = (data or {}).get('draft_conflicts', [])
    names = {name.casefold(): name for name, _value in sent}
    if not isinstance(raw, list) or len(raw) > MAX_DRAFT_CONFLICTS \
            or not all(isinstance(item, str) and item.casefold() in names for item in raw):
        raise queue_prep.invalid('draft_conflicts')
    return tuple(dict.fromkeys(names[item.casefold()] for item in raw))


def audio_params(params, output_dir, result_path, scripts_dir) -> Dict[str, Any]:
    """FR-SA-2: an audio job's prep params (one duplicate over ``[r0, end + 1)``), from ``JobParams`` only."""
    run = params.audio
    data = queue_prep.prep_params(params, output_dir, result_path, scripts_dir)
    data.pop('frame', None)
    data.update(mode='prepare_audio', range=[run.first, run.end + 1], start=run.start)
    return data


def frame_rate(data: Optional[dict]) -> Tuple[int, int]:
    """The prep's ``frame_rate`` ``(num, den)`` (the sequence's display rate); ``JobFailure`` when invalid."""
    try:
        return shots_result.check_rate((data or {}).get('frame_rate'), 'frame_rate')
    except shots_result.ShotsResultError:
        raise JobFailure(str(queue_prep.invalid('frame_rate'))) from None


def export_params(params, export_dir, result_path, scripts_dir) -> Dict[str, Any]:
    """FR-SA-9: the export commandlet's params (``range`` ``[r0, end + 1)``), from ``JobParams`` only."""
    run = params.audio
    return {'schema': SCHEMA, 'job_id': params.job_id, 'mode': 'export_audio', 'map': params.map,
            'sequence': params.sequence, 'range': [run.first, run.end + 1], 'export_dir': str(export_dir),
            'result_path': str(result_path), 'scripts_dir': str(scripts_dir), 'max_sections': MAX_SECTIONS,
            'max_export_bytes': MAX_EXPORT_BYTES}


class SourcesError(ValueError):
    """``audio_sources.json`` breaks a rule; ``field`` names it."""

    def __init__(self, field: str):
        self.field = field
        super().__init__(f"the sound export returned an invalid result ({field})")


@dataclass(frozen=True)
class SourceRow:
    start: int
    end: int
    offset: int
    volume: float
    looping: bool
    wav: str
    sample_rate: int


def _source_row(row, index: int, r0: int, end: int) -> Optional[SourceRow]:
    """One checked section; ``None`` for an unsupported one (counted by the caller)."""
    field = f'sections[{index}]'
    if isinstance(row, dict) and set(row) == {'unsupported'} and isinstance(row['unsupported'], str):
        return None
    if not isinstance(row, dict) or set(row) != set(SOURCE_KEYS):
        raise SourcesError(field)
    ints = all(isinstance(row[k], int) and not isinstance(row[k], bool) for k in ('start', 'end', 'sample_rate'))
    if not ints or not -FRAME_MAX <= row['start'] < row['end'] <= FRAME_MAX or row['start'] >= end or row['end'] <= r0:
        raise SourcesError(f'{field}.start')
    offset, volume = row['start_offset_samples'], row['volume']
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= MAX_OFFSET_SAMPLES:
        raise SourcesError(f'{field}.start_offset_samples')
    if not isinstance(volume, (int, float)) or isinstance(volume, bool) or not 0 <= volume <= 4:
        raise SourcesError(f'{field}.volume')
    if not isinstance(row['looping'], bool):
        raise SourcesError(f'{field}.looping')
    if not isinstance(row['wav'], str) or not _WAV_NAME.fullmatch(row['wav']):
        raise SourcesError(f'{field}.wav')
    if not 8_000 <= row['sample_rate'] <= 192_000:
        raise SourcesError(f'{field}.sample_rate')
    return SourceRow(row['start'], row['end'], offset, float(volume), row['looping'], row['wav'], row['sample_rate'])


def parse_sources(data: Optional[dict], r0: int, end: int) -> Tuple[Tuple[SourceRow, ...], int]:
    """FR-SA-9: ``(supported sections, unsupported count)`` of the export result for the run ``[r0, end)``."""
    if data is None:
        raise SourcesError('result')
    if data.get('ok') is not True:
        raise SourcesError('ok')
    raw = data.get('sections')
    if not isinstance(raw, list) or len(raw) > MAX_SECTIONS:
        raise SourcesError('sections')
    rows = [_source_row(row, index, r0, end) for index, row in enumerate(raw)]
    return tuple(row for row in rows if row is not None), sum(row is None for row in rows)


def shots_params(params, result_path: str, scripts_dir: str) -> Dict[str, Any]:
    """FR-SD-4: the ``list_shots`` params; ``scripts_dir`` is the job's ``scripts\\`` (the shots and facts modules)."""
    return {'schema': SCHEMA, 'job_id': params.job_id, 'mode': 'list_shots', 'map': params.map,
            'sequence': params.sequence, 'preset': params.preset, 'result_path': str(result_path),
            'scripts_dir': str(scripts_dir), 'max_shots': MAX_SHOTS}


def _warnings(data: dict):
    raw = data.get('warnings', [])
    if not isinstance(raw, list):
        raise shots_result.ShotsResultError('warnings')
    return [queue_prep.sanitize(item, shots_result.WARNING_MAX_CHARS) for item in raw[:shots_result.MAX_WARNINGS]]


def _facts(data: dict):
    facts = data.get('preset_facts')
    if not isinstance(facts, dict):
        return facts  # the shared check names the field
    facts = dict(facts)
    for key, allowed in _WORDS.items():
        if key in facts:
            facts[key] = queue_prep.allowed_word(facts[key], allowed)
    return facts


@dataclass(frozen=True)
class Discovery:
    preset_kind: str
    playback_range: Tuple[int, int]
    frame_rate: Tuple[int, int]
    shot_source: str
    shots: Tuple[Tuple[str, int, int], ...]
    preset_facts: Dict[str, Any]
    warnings: Tuple[str, ...] = ()

    def body(self) -> Dict[str, Any]:
        """The FR-SD-8 ``shots-result/`` body (``shared.unreal.shots_result``), as checked and cleaned here."""
        return {'preset_kind': self.preset_kind, 'playback_range': list(self.playback_range),
                'frame_rate': list(self.frame_rate), 'shot_source': self.shot_source,
                'shots': [list(row) for row in self.shots], 'preset_facts': dict(self.preset_facts),
                'warnings': list(self.warnings)}


def parse_shots(data: Optional[dict]) -> Discovery:
    """FR-SD-7: the strict check of a ``list_shots`` result; raises ``JobFailure``."""
    if data is None:
        raise JobFailure("Shot discovery wrote no readable result.")
    if data.get('ok') is not True:
        raise JobFailure(queue_prep.sanitize(data.get('error') or 'Shot discovery failed without an error message.'))
    try:
        body = {key: data.get(key) for key in shots_result.TOP_KEYS}
        body['warnings'], body['preset_facts'] = _warnings(data), _facts(data)
        checked = shots_result.check_shots_result(body, refuse_names=False)
    except shots_result.ShotsResultError as exc:
        raise JobFailure(str(exc)) from None
    return Discovery(preset_kind=checked['preset_kind'],
                     playback_range=(checked['playback_start'], checked['playback_end']),
                     frame_rate=(checked['frame_rate_num'], checked['frame_rate_den']),
                     shot_source=checked['shot_source'],
                     shots=tuple((row['name'], row['start'], row['end']) for row in checked['shots']),
                     preset_facts=checked['preset_facts'], warnings=tuple(checked['warnings']))
