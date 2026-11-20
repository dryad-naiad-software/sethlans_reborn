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
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from sethlans_worker_agent.unreal_executor import queue_prep
from sethlans_worker_agent.unreal_executor.outcomes import JobFailure
from shared.unreal import shots_result

SCHEMA = queue_prep.SCHEMA
MAX_SHOTS = shots_result.MAX_SHOTS
_WORDS = {'game_overrides': queue_prep.GAME_OVERRIDES, 'texture_streaming': queue_prep.TEXTURE_STREAMING}


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
