# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The shot discovery result (Unreal sequence rendering FR-SD-5..8): its keys,
its bounds and the shot-name rules, in one place for the worker that sends
it and the manager that checks it again.

The body of ``POST /api/jobs/{id}/shots-result/`` is the prep's
``list_shots`` report (FR-SD-5) after the worker's strict parse. Every key
is required and no other key is allowed, at any level::

    {
      "preset_kind": "legacy" | "graph",
      "playback_range": [start, end],        # edit frames, end exclusive
      "frame_rate": [num, den],              # the sequence's display rate
      "shot_source": "shot_tracks" | "camera_cuts",
      "shots": [[name, start, end], ...],    # edit order, end exclusive
      "preset_facts": {
        "resolution": [w, h] | null, "temporal_samples": n, "spatial_samples": n,
        "layers_ok": b, "can_png": b, "can_exr": b, "can_sound": b,
        "game_overrides": s, "texture_streaming": s,
        "custom_frame_rate": [num, den] | null, "frame_step": n | null,
        "handle_frames": n | null, "frame_number_offset": n | null,
        "zero_pad": n | null
      },
      "warnings": [s, ...]                   # at most 64, each at most 512 characters
    }

Shot names (FR-SD-6) are cleaned by both sides with :func:`clean_shot_names`.
The manager also refuses a name a cleaning worker can never send
(:func:`refused_shot_name`). Pure stdlib; nothing here touches the disk.
"""

import re

from .render_context import GAME_OVERRIDES_VALUES, TEXTURE_STREAMING_VALUES

TOP_KEYS = ('preset_kind', 'playback_range', 'frame_rate', 'shot_source', 'shots', 'preset_facts', 'warnings')
FACT_KEYS = (
    'resolution', 'temporal_samples', 'spatial_samples', 'layers_ok', 'can_png', 'can_exr', 'can_sound',
    'game_overrides', 'texture_streaming', 'custom_frame_rate', 'frame_step', 'handle_frames',
    'frame_number_offset', 'zero_pad',
)
PRESET_KINDS = ('graph', 'legacy')
SHOT_SOURCES = ('camera_cuts', 'shot_tracks')

FRAME_MAX = 10_000_000
RATE_NUM_MAX = 1_000_000
RATE_DEN_MAX = 1_001
FPS_MAX = 120
MAX_SHOTS = 2_000
MAX_WARNINGS = 64
WARNING_MAX_CHARS = 512
SHOT_NAME_MAX = 64
RAW_NAME_MAX = 512
RESOLUTION_MAX = 32_768
SAMPLES_MAX = 10_000
# Bounds of the D5b output settings; ``None`` means the preset kind has no such setting.
_OPTIONAL_INT_FACTS = {
    'frame_step': (1, 10_000),
    'handle_frames': (0, 100_000),
    'frame_number_offset': (-FRAME_MAX, FRAME_MAX),
    'zero_pad': (0, 32),
}
_BOOL_FACTS = ('layers_ok', 'can_png', 'can_exr', 'can_sound')

_NAME_REFUSED_CHARS = re.compile(r'[^A-Za-z0-9_.\-]')
_UNSAFE_TEXT = re.compile('[\x00-\x1f\x7f-\x9f\ud800-\udfff]')
NEGATIVE_START_MESSAGE = "Sequences that start before frame 0 are not supported."


class ShotsResultError(ValueError):
    """A result that breaks a rule; ``field`` names the first bad field."""

    def __init__(self, field, message=None):
        self.field = field
        super().__init__(message or f"Shot discovery returned an invalid result ({field}).")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _int_in(value, low, high):
    return _is_int(value) and low <= value <= high


def clean_shot_name(raw, ordinal):
    """One name by FR-SD-6: ``[A-Za-z0-9_.-]``, at most 64, no leading ``.`` or ``-``.

    An empty name, ``.`` or ``..`` becomes ``shot_<nnn>`` (``ordinal`` is 1-based).
    """
    name = _NAME_REFUSED_CHARS.sub('_', raw if isinstance(raw, str) else '')[:SHOT_NAME_MAX]
    if name in ('', '.', '..'):
        return f'shot_{ordinal:03d}'
    if name[0] in '.-':
        name = '_' + name[1:]
    return name


def clean_shot_names(names):
    """Clean every name, then give duplicates ``-2``, ``-3``, ... (case-insensitive)."""
    result, seen = [], set()
    for ordinal, raw in enumerate(names, start=1):
        base = clean_shot_name(raw, ordinal)
        name, count = base, 1
        while name.casefold() in seen:
            count += 1
            suffix = f'-{count}'
            name = base[:SHOT_NAME_MAX - len(suffix)] + suffix
        seen.add(name.casefold())
        result.append(name)
    return result


def refused_shot_name(raw):
    """Why the manager refuses a name a cleaning worker never sends, else ``None``."""
    if not isinstance(raw, str) or not 1 <= len(raw) <= RAW_NAME_MAX:
        return f"must be a string of 1-{RAW_NAME_MAX} characters"
    if raw in ('.', '..'):
        return "may not be '.' or '..'"
    if raw[0] in '.-':
        return "may not start with '.' or '-'"
    return None


def _check_keys(data, allowed, field):
    if not isinstance(data, dict):
        raise ShotsResultError(field or 'body')
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise ShotsResultError(f'{field}.{unknown[0]}' if field else unknown[0])
    missing = [key for key in allowed if key not in data]
    if missing:
        raise ShotsResultError(f'{field}.{missing[0]}' if field else missing[0])


def check_rate(value, field):
    """A ``[num, den]`` pair inside the FR-SD-7 bounds; returns it as a tuple."""
    if not (isinstance(value, list) and len(value) == 2
            and _int_in(value[0], 1, RATE_NUM_MAX) and _int_in(value[1], 1, RATE_DEN_MAX)):
        raise ShotsResultError(field)
    num, den = value
    if not den <= num <= FPS_MAX * den:
        raise ShotsResultError(field)
    return num, den


def _check_range(value):
    if not (isinstance(value, list) and len(value) == 2 and all(_is_int(v) for v in value)):
        raise ShotsResultError('playback_range')
    start, end = value
    if start < 0:
        raise ShotsResultError('playback_range', NEGATIVE_START_MESSAGE)
    if not start < end <= FRAME_MAX:
        raise ShotsResultError('playback_range')
    return start, end


def _check_shot_rows(rows, start, end):
    """``[[name, start, end], ...]``: 1-2,000, sorted, non-overlapping, inside the range."""
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_SHOTS:
        raise ShotsResultError('shots')
    previous_end = start
    for index, row in enumerate(rows):
        field = f'shots[{index}]'
        if not (isinstance(row, list) and len(row) == 3 and _is_int(row[1]) and _is_int(row[2])):
            raise ShotsResultError(field)
        if not previous_end <= row[1] < row[2] <= end:
            raise ShotsResultError(field)
        previous_end = row[2]
    return rows


def clean_text(text):
    """Free text without control characters (NUL included) or lone surrogates.

    PostgreSQL refuses ``\\u0000`` in a JSON or text column, and a lone
    surrogate cannot be encoded as UTF-8.
    """
    return _UNSAFE_TEXT.sub('', text)


def _check_warnings(value):
    if not isinstance(value, list) or len(value) > MAX_WARNINGS:
        raise ShotsResultError('warnings')
    for index, text in enumerate(value):
        if not isinstance(text, str) or len(text) > WARNING_MAX_CHARS:
            raise ShotsResultError(f'warnings[{index}]')
    return [clean_text(text) for text in value]


def _check_resolution(value):
    """``[w, h]``, or ``None`` when the prep could not read the preset's size."""
    if value is None:
        return None
    if not (isinstance(value, list) and len(value) == 2 and all(_int_in(v, 1, RESOLUTION_MAX) for v in value)):
        raise ShotsResultError('preset_facts.resolution')
    return list(value)


def _check_simple_facts(facts):
    for key in ('temporal_samples', 'spatial_samples'):
        if not _int_in(facts[key], 1, SAMPLES_MAX):
            raise ShotsResultError(f'preset_facts.{key}')
    for key in _BOOL_FACTS:
        if not isinstance(facts[key], bool):
            raise ShotsResultError(f'preset_facts.{key}')
    if facts['game_overrides'] not in GAME_OVERRIDES_VALUES:
        raise ShotsResultError('preset_facts.game_overrides')
    if facts['texture_streaming'] not in TEXTURE_STREAMING_VALUES:
        raise ShotsResultError('preset_facts.texture_streaming')
    for key, (low, high) in _OPTIONAL_INT_FACTS.items():
        if facts[key] is not None and not _int_in(facts[key], low, high):
            raise ShotsResultError(f'preset_facts.{key}')


def check_preset_facts(facts):
    """The FR-SD-5 ``preset_facts`` object; returns a new dict."""
    _check_keys(facts, FACT_KEYS, 'preset_facts')
    _check_simple_facts(facts)
    result = {key: facts[key] for key in FACT_KEYS}
    result['resolution'] = _check_resolution(facts['resolution'])
    custom = facts['custom_frame_rate']
    result['custom_frame_rate'] = None if custom is None else list(check_rate(custom, 'preset_facts.custom_frame_rate'))
    return result


def check_shots_result(body, *, refuse_names=True):
    """Check a whole result (FR-SD-7, FR-SD-8); return it normalised.

    Returns ``{'preset_kind', 'playback_start', 'playback_end', 'frame_rate_num',
    'frame_rate_den', 'shot_source', 'shots': [{'index', 'name', 'start', 'end'}],
    'preset_facts', 'warnings'}`` with 1-based shot indexes and cleaned names.
    ``refuse_names`` (the manager) also refuses names :func:`refused_shot_name` names.

    Raises:
        ShotsResultError: naming the first bad field.
    """
    _check_keys(body, TOP_KEYS, '')
    if body['preset_kind'] not in PRESET_KINDS:
        raise ShotsResultError('preset_kind')
    if body['shot_source'] not in SHOT_SOURCES:
        raise ShotsResultError('shot_source')
    start, end = _check_range(body['playback_range'])
    num, den = check_rate(body['frame_rate'], 'frame_rate')
    rows = _check_shot_rows(body['shots'], start, end)
    if refuse_names:
        for index, row in enumerate(rows):
            reason = refused_shot_name(row[0])
            if reason is not None:
                raise ShotsResultError(f'shots[{index}].name', f"Shot name {index + 1} {reason}.")
    names = clean_shot_names([row[0] for row in rows])
    return {
        'preset_kind': body['preset_kind'], 'playback_start': start, 'playback_end': end,
        'frame_rate_num': num, 'frame_rate_den': den, 'shot_source': body['shot_source'],
        'shots': [{'index': i, 'name': name, 'start': row[1], 'end': row[2]}
                  for i, (name, row) in enumerate(zip(names, rows), start=1)],
        'preset_facts': check_preset_facts(body['preset_facts']),
        'warnings': _check_warnings(body['warnings']),
    }
