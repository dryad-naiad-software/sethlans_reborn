# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The form of ``POST /api/jobs/{id}/sound-result/`` (Unreal sequence
rendering FR-SA-6): the OpenAPI schema and the strict checks of the
fields, so DRF never coerces a value.  The fields, their bounds and the
``offset`` object are ``shared.unreal.sound_result``, the module the
worker builds them with:

* ``file`` (required): the run's one ``.wav`` (checked by
  ``services/sequence_sound_result.check_wav``);
* ``offset`` (required): the FR-SA-10 object as JSON text of at most 16 KiB;
* ``latency_compensation_skipped`` (required): ``true`` or ``false``;
* ``render_s`` (optional): the render's seconds as a plain decimal;
* ``summary`` (optional): text of at most 32 KiB.
"""

from rest_framework import serializers

from shared.unreal.sound_result import (
    FORM_FIELDS, REQUIRED_FIELDS, SUMMARY_MAX_BYTES, SoundResultError, parse_flag, parse_offset_text, parse_seconds,
)

from ..constants_sequence import SOUND_APPLIED_SOURCES, SOUND_FLAG_VALUES


def _offset(text, rate):
    """The ``offset`` object; a refusal inside it names the bad key as ``offset.<key>``."""
    try:
        return parse_offset_text(text, rate)
    except SoundResultError as exc:
        if exc.field == 'offset':
            raise
        raise SoundResultError('offset', f"offset.{exc.field} is invalid.") from None


def parse_form(data, rate):
    """``{offset, latency_compensation_skipped, render_s, summary}`` from the form; :class:`SoundResultError`."""
    summary = data.get('summary', '')
    if len(summary.encode('utf-8', 'surrogatepass')) > SUMMARY_MAX_BYTES:
        raise SoundResultError('summary', "summary must be text of at most 32 KiB.")
    render_s = data.get('render_s')
    return {
        'offset': _offset(data['offset'], rate),
        'latency_compensation_skipped': parse_flag(data['latency_compensation_skipped']),
        'render_s': None if render_s is None else parse_seconds(render_s),
        'summary': summary,
    }


def form_problem(data):
    """Unknown or missing fields, a field sent twice, or a ``file`` that is not one (400 text), else ``None``."""
    unknown = sorted(set(data.keys()) - set(FORM_FIELDS))
    if unknown:
        return f"Unknown fields: {', '.join(unknown[:5])}."
    missing = [name for name in REQUIRED_FIELDS if name not in data]
    if missing:
        return f"Send {', '.join(missing)}."
    if any(len(data.getlist(name)) != 1 for name in data.keys()):
        return "Send each field once."
    if not hasattr(data['file'], 'size') or any(hasattr(data[name], 'size') for name in data.keys() if name != 'file'):
        return "Only file may be a file."
    return None


class SoundResultRequestSerializer(serializers.Serializer):
    """The multipart fields of ``sound-result/`` (schema only)."""

    file = serializers.FileField(help_text="The run's one .wav: 16-bit PCM, 1-8 channels, at most 4 GiB.")
    offset = serializers.CharField(
        help_text="The measured offset object (FR-SA-10) as JSON text, at most 16 KiB; see shared.unreal.sound_result.",
    )
    latency_compensation_skipped = serializers.ChoiceField(
        choices=list(SOUND_FLAG_VALUES), help_text="Whether MRG skipped its latency compensation (information only).",
    )
    render_s = serializers.CharField(required=False, help_text="The render's seconds as a plain decimal.")
    summary = serializers.CharField(required=False, allow_blank=True, help_text="The worker's summary, at most 32 KiB.")


class SoundResultResponseSerializer(serializers.Serializer):
    """``sound-result/`` success body."""

    status = serializers.CharField(help_text="Always DONE.")
    applied_samples = serializers.IntegerField(
        help_text="The correction the run's sound cuts use, in samples (positive: the sound leads the picture).",
    )
    applied_source = serializers.ChoiceField(
        choices=list(SOUND_APPLIED_SOURCES),
        help_text="measured, the preset's override (when the measurement was not usable), or none.",
    )
