# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
An audio job's result (Unreal sequence rendering FR-SA-1, FR-SA-5, FR-SA-6,
FR-SA-10, FR-SA-11, FR-SV-5, D24): the run's shots on the job row, the
``.wav`` rules, the fields of ``POST /api/jobs/{id}/sound-result/`` and the
D24 rules, in one place for the worker that builds them and the manager
that checks them again.

The audio job's ``render_settings`` carry, besides map, sequence and preset,
the run's shot parts under ``RUN_SHOTS_KEY`` (``unreal.piece_shots``):
``[[index, start, end], ...]`` in edit order, end exclusive, covering the
run ``start_frame..end_frame`` without a gap. The worker correlates those
shots and names them in the ``offset`` object by their index; the manager
maps an index to its ``SequenceShot`` name.

``multipart/form-data`` fields (``FORM_FIELDS``; ``REQUIRED_FIELDS`` must be
there): ``file`` (the run's one ``.wav``), ``offset`` (JSON text of at most
16 KiB), ``latency_compensation_skipped`` (``"true"`` / ``"false"``),
``render_s`` (a plain decimal of at most 86,400, optional) and ``summary``
(at most 32 KiB, optional). The ``offset`` object; every key is required and
no other key is allowed, at any level::

    {
      "status": "measured" | "low_confidence" | "unavailable",
      "offset_samples": int | null,     # the confident shots' median lag; an int when measured;
                                        # |n| <= 10 x the .wav's rate
      "reason": str,                    # at most 512 characters; "" when measured
      "shots_tried": 0-16, "shots_confident": 0-16, "shots_agreeing": 0-16,
      "min_peak": 0-1 | null,           # the lowest sample-stage peak of the confident shots
      "spread_samples": int >= 0 | null,  # max - min lag of the confident shots
      "unsupported_sections": 0-64,
      "export_s": 0-86400 | null, "correlate_s": 0-86400 | null,
      "shots": [                        # at most 16, in edit order
        {"shot": index,                 # the shot's index (RUN_SHOTS_KEY), 1-2,000
         "lag_samples": int,            # |n| <= 20 x rate; rendered sample p holds mix sample p + lag
                                        # (positive: sound leads)
         "envelope_peak": 0-1, "envelope_margin": 0-2, "peak": 0-1, "ambiguous": bool}
      ]
    }

Pure stdlib; nothing here touches the disk.
"""
import json
import math
import re
import statistics

from .shots_result import MAX_SHOTS as MAX_SHOT_INDEX

RUN_SHOTS_KEY = 'unreal.piece_shots'
FORM_FIELDS = ('file', 'offset', 'latency_compensation_skipped', 'render_s', 'summary')
REQUIRED_FIELDS = ('file', 'offset', 'latency_compensation_skipped')
OFFSET_KEYS = ('status', 'offset_samples', 'reason', 'shots_tried', 'shots_confident', 'shots_agreeing', 'min_peak',
               'spread_samples', 'unsupported_sections', 'export_s', 'correlate_s', 'shots')
SHOT_KEYS = ('shot', 'lag_samples', 'envelope_peak', 'envelope_margin', 'peak', 'ambiguous')
MEASURED, LOW_CONFIDENCE, UNAVAILABLE = 'measured', 'low_confidence', 'unavailable'
STATUSES = (MEASURED, LOW_CONFIDENCE, UNAVAILABLE)
MAX_SHOTS, MAX_SECTIONS = 16, 64
REASON_MAX = 512
OFFSET_MAX_BYTES = 16 * 1024
SUMMARY_MAX_BYTES = 32 * 1024
MAX_OFFSET_SECONDS = 10
MAX_SECONDS = 86_400
PEAK_MIN = 0.80                    # a confident shot's sample-stage peak (FR-SA-10)
AGREE_SAMPLES = 64                 # two 32-sample callbacks (D24)
LENGTH_SLACK = 256                 # FR-SA-5
CHANNELS = (1, 8)
RATES = (8_000, 192_000)
_SECONDS = re.compile(r'[0-9]{1,5}(\.[0-9]{1,6})?', re.ASCII)


class SoundResultError(ValueError):
    """A field that breaks a rule; ``field`` names it."""

    def __init__(self, field, message=None):
        self.field = field
        super().__init__(message or f"Invalid sound result ({field}).")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value, low, high):
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            and low <= value <= high)


LATENCY_FRAMES = 7                 # MRG's latency compensation (MovieGraphDefaultAudioRenderer.cpp:99-160)


def expected_samples(frames, rate, num, den, latency_skipped=False):
    """FR-SA-5: the samples ``frames`` frames last at ``rate`` Hz and ``num/den`` frames per second. When MRG skipped
    its head compensation (``latency_compensation_skipped``) it still drains 7 frames at the tail, so the file is
    7 frames longer; those samples lie after the run and the cuts, which count from ``r0``, never reach them."""
    return round((frames + (LATENCY_FRAMES if latency_skipped else 0)) * rate * den / num)


def check_wav_params(channels, width, rate, samples, expected):
    """FR-SA-5 on a ``.wav`` header: 16-bit PCM, 1-8 channels, 8-192 kHz, within 256 samples of ``expected``."""
    if width != 2:
        raise SoundResultError('file', "The sound file is not 16-bit PCM.")
    if not CHANNELS[0] <= channels <= CHANNELS[1]:
        raise SoundResultError('file', f"The sound file has {channels} channels; 1-8 are allowed.")
    if not RATES[0] <= rate <= RATES[1]:
        raise SoundResultError('file', f"The sound file's rate {rate} Hz is outside 8,000-192,000 Hz.")
    if abs(samples - expected) > LENGTH_SLACK:
        raise SoundResultError('file', f"The sound file has {samples} samples; the run needs {expected} "
                                       f"(within {LENGTH_SLACK}).")


def parse_flag(text):
    if text not in ('true', 'false'):
        raise SoundResultError('latency_compensation_skipped', "latency_compensation_skipped must be true or false.")
    return text == 'true'


def parse_seconds(text):
    if not isinstance(text, str) or not _SECONDS.fullmatch(text) or float(text) > MAX_SECONDS:
        raise SoundResultError('render_s', "render_s must be a plain decimal number of seconds up to 86,400.")
    return float(text)


def _keys(data, allowed, field):
    if not isinstance(data, dict) or set(data) != set(allowed):
        raise SoundResultError(field)


def _optional(data, key, check):
    if data[key] is not None and not check(data[key]):
        raise SoundResultError(key)


def _check_shot(row, index, limit):
    field = f'shots[{index}]'
    _keys(row, SHOT_KEYS, field)
    rules = {'shot': lambda v: _is_int(v) and 1 <= v <= MAX_SHOT_INDEX,
             'lag_samples': lambda v: _is_int(v) and abs(v) <= 2 * limit,
             'envelope_peak': lambda v: _is_number(v, 0, 1), 'envelope_margin': lambda v: _is_number(v, 0, 2),
             'peak': lambda v: _is_number(v, 0, 1), 'ambiguous': lambda v: isinstance(v, bool)}
    for key, check in rules.items():
        if not check(row[key]):
            raise SoundResultError(f'{field}.{key}')


def check_offset(data, rate):
    """The strict FR-SA-6 check of an ``offset`` object for a ``.wav`` of ``rate`` Hz; returns ``data``."""
    _keys(data, OFFSET_KEYS, 'offset')
    if data['status'] not in STATUSES:
        raise SoundResultError('status')
    limit = MAX_OFFSET_SECONDS * int(rate)
    _optional(data, 'offset_samples', lambda v: _is_int(v) and abs(v) <= limit)
    if data['status'] == MEASURED and data['offset_samples'] is None:
        raise SoundResultError('offset_samples')
    if not isinstance(data['reason'], str) or len(data['reason']) > REASON_MAX:
        raise SoundResultError('reason')
    for key in ('shots_tried', 'shots_confident', 'shots_agreeing'):
        if not (_is_int(data[key]) and 0 <= data[key] <= MAX_SHOTS):
            raise SoundResultError(key)
    if not (_is_int(data['unsupported_sections']) and 0 <= data['unsupported_sections'] <= MAX_SECTIONS):
        raise SoundResultError('unsupported_sections')
    _optional(data, 'min_peak', lambda v: _is_number(v, 0, 1))
    _optional(data, 'spread_samples', lambda v: _is_int(v) and 0 <= v <= 2 * limit)
    for key in ('export_s', 'correlate_s'):
        _optional(data, key, lambda v: _is_number(v, 0, MAX_SECONDS))
    shots = data['shots']
    if not isinstance(shots, list) or len(shots) > MAX_SHOTS:
        raise SoundResultError('shots')
    for index, row in enumerate(shots):
        _check_shot(row, index, limit)
    return data


def _no_constant(name):
    raise ValueError(name)


def parse_offset_text(text, rate):
    """The ``offset`` form field (JSON text, at most 16 KiB, no NaN or Infinity) checked by :func:`check_offset`."""
    if not isinstance(text, str) or len(text.encode('utf-8', 'surrogatepass')) > OFFSET_MAX_BYTES:
        raise SoundResultError('offset', "offset must be JSON text of at most 16 KiB.")
    try:
        data = json.loads(text, parse_constant=_no_constant)
    except ValueError:
        raise SoundResultError('offset', "offset is not valid JSON.") from None
    return check_offset(data, rate)


def offset_text(data):
    """The ``offset`` form field the worker sends (compact JSON, no NaN)."""
    return json.dumps(data, allow_nan=False, separators=(',', ':'))


def confident(row):
    """D24: a shot whose envelope peak is unambiguous and whose sample-stage peak is at least 0.80."""
    return not row['ambiguous'] and row['peak'] >= PEAK_MIN


def disagreeing_shots(data):
    """FR-SA-11: the indexes of the confident shots more than 64 samples from the run's offset (the confident
    shots' median when the offset is not set), in edit order."""
    rows = [row for row in data['shots'] if confident(row)]
    if not rows:
        return []
    center = data['offset_samples']
    if center is None:
        center = statistics.median_low(row['lag_samples'] for row in rows)
    return [row['shot'] for row in rows if abs(row['lag_samples'] - center) > AGREE_SAMPLES]


def applied_offset(data, override_ms, rate):
    """FR-SV-5 / D24: ``(samples, source)``: the measured offset, else the preset's override (ms), else 0."""
    if data['status'] == MEASURED and data['offset_samples'] is not None:
        return data['offset_samples'], 'measured'
    if override_ms is not None:
        return round(override_ms * rate / 1000), 'override'
    return 0, 'none'
