# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
One sound run's record and its warnings (Unreal sequence rendering
FR-SA-11, D15, D18, D24).  Pure functions, no database access.

The correction a run's cuts use (``applied_samples``, positive = the sound
leads the picture):

* ``measured`` -- the run's measured ``offset_samples``;
* otherwise the preset's override (``Project.unreal_sound_offsets``, copied
  to the submission as ``sound_offset_ms`` for its preset and rate), as
  ``round(ms * rate / 1000)``, when one is set within +-10,000 ms
  (``override``);
* otherwise 0 (``none``).

A run that is not ``measured`` gets the D24 warning naming its shots, the
status, the reason and the correction used.  A ``measured`` run whose
confident shots lie more than 64 samples from its offset gets the
disagreement warning naming them.  Both rules are
``shared.unreal.sound_result``'s (``applied_offset``,
``disagreeing_shots``), the ones the worker uses; shots are reported by
index and named from the run's shots.  The worker's text is untrusted: it is
cleaned of control characters and NUL, and every warning is capped.
"""

from shared.unreal.shots_result import clean_text
from shared.unreal.sound_result import MEASURED, applied_offset, disagreeing_shots

from ..constants_sequence import SOUND_APPLIED_SOURCES, SOUND_OFFSET_MS_MAX

WARNING_MAX_CHARS = 1000
NAMES_MAX = 16
APPLIED_MEASURED, APPLIED_OVERRIDE, APPLIED_NONE = SOUND_APPLIED_SOURCES

NOT_LINED_UP = "Sound for shots {first}–{last} could not be lined up automatically ({status}: {reason}); "
NO_CORRECTION = "no correction was applied. Set a sound offset for preset {preset} to correct it."
OVERRIDE_USED = "the preset's sound offset of {ms} ms was applied."
DISAGREEING = "Sound offset: shots {names} differ from the run's offset by up to {ms} ms; check the sound there."
SILENT = "The sound render is silent."


def override_ms(value):
    """The submission's ``sound_offset_ms`` when it is a usable override, else ``None`` (D18 bounds)."""
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) <= SOUND_OFFSET_MS_MAX:
        return value
    return None


def applied(offset, rate, override):
    """``(applied_samples, applied_source)`` for a checked ``offset`` object (D24)."""
    return applied_offset(offset, override_ms(override), rate)


def _cap(text):
    return clean_text(text)[:WARNING_MAX_CHARS]


def _disagreement(offset, rate, run_names):
    shots = disagreeing_shots(offset)
    if not shots:
        return []
    lags = {row['shot']: row['lag_samples'] for row in offset['shots']}
    worst = max(abs(lags[shot] - offset['offset_samples']) for shot in shots)
    listed = ', '.join(run_names.get(shot, f'#{shot}') for shot in shots[:NAMES_MAX])
    return [_cap(DISAGREEING.format(names=listed, ms=round(worst * 1000 / rate)))]


def run_warnings(offset, rate, source, applied_samples, run_names, preset):
    """The D24 warnings of one run; ``run_names`` maps the run's shot indexes to names, in edit order."""
    if offset['status'] == MEASURED:
        return _disagreement(offset, rate, run_names)
    names = list(run_names.values())
    first, last = (names[0], names[-1]) if names else ('?', '?')
    reason = clean_text(offset['reason']).strip() or 'no reason given'
    head = NOT_LINED_UP.format(first=first, last=last, status=offset['status'], reason=reason)
    if source == APPLIED_OVERRIDE:
        tail = OVERRIDE_USED.format(ms=round(applied_samples * 1000 / rate))
    else:
        tail = NO_CORRECTION.format(preset=clean_text(preset))
    return [_cap(head + tail)]


def run_entry(job, offset, rate, override, extra):
    """The FR-SA-11 ``sound_runs`` entry of an audio ``job`` (run ``[start, end)`` from ``r0``)."""
    samples, source = applied(offset, rate, override)
    entry = {
        'job': job.pk, 'start': job.start_frame, 'end': job.end_frame + 1,
        'r0': job.start_frame - job.unreal_lead_in_frames, 'rate': rate, 'offset': offset,
        'applied_samples': samples, 'applied_source': source,
    }
    entry.update(extra)
    return entry


def merged_runs(runs, entry):
    """``sound_runs`` with ``entry`` replacing the one of the same job, ordered by start."""
    kept = [run for run in runs or [] if isinstance(run, dict) and run.get('job') != entry['job']]
    return sorted(kept + [entry], key=lambda run: (run.get('start', 0), run.get('job', 0)))


def merged_warnings(warnings, old_entry, new_warnings, limit):
    """The submission's warnings without the run's earlier ones, then its new ones; deduplicated, capped.

    When the cap is reached the older warnings give way, so a run's sound warning is never lost.
    """
    fresh = list(dict.fromkeys(new_warnings))[:limit]
    stale = set((old_entry or {}).get('warnings') or []) | set(fresh)
    kept = list(dict.fromkeys(w for w in warnings or [] if w not in stale))
    return kept[:limit - len(fresh)] + fresh
