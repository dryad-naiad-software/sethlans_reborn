# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Submission rules of Unreal sequence rendering (spec FR-SQ-3, FR-SQ-5..9,
D5b, D19).

Two stages:

* :func:`request_errors` -- the body alone (selection shape, resolution
  pair, EXR with video, merge without sound or video, draft keys and
  values), at create;
* :func:`facts_errors` -- the rules that need a DONE discovery (known shot
  names, the frame range inside the playback range, the preset's custom
  frame rate, EXR and sound support, draft samples, an even video size).
  They run at create when the discovery is DONE, else at planning, where a
  failure sets the submission ERROR with the same text.

Both return ``{field: message}``; the caller raises or stores them.
"""

from ..constants_sequence import FrameFormat

DRAFT_SWITCHES = ('motion_blur', 'ray_traced_shadows', 'ray_traced_reflections')
DRAFT_SAMPLES = ('temporal_samples', 'spatial_samples')
DRAFT_KEYS = DRAFT_SAMPLES + ('screen_percentage',) + DRAFT_SWITCHES
SCREEN_PERCENTAGE_RANGE = (10, 100)

ONE_SELECTION = "Choose shots or a frame range, not both."
RANGE_PAIR = "Send both frame_start and frame_end, or neither."
RANGE_ORDER = "frame_start must not be after frame_end."
RESOLUTION_PAIR = "Send both resolution_x and resolution_y, or neither."
MERGE_NEEDS = "merge_sound needs sound and video_settings."
READ_SHOTS_FIRST = "Read the shots first (POST /api/sequence-discoveries/), then choose shots."
NO_EXR = "The preset has no EXR output node; add one to render EXR."
NO_SOUND = "The preset has no audio output; add one to render sound."
NO_SIZE = "The preset's output size could not be read; set resolution_x and resolution_y to make a video."
STEP_OR_HANDLES = "The preset sets a frame step or handle frames; the farm renders every frame without handles."


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _draft_value_error(key, value):
    if key in DRAFT_SWITCHES:
        return None if value is False else f"draft.{key}: only false is allowed."
    if key == 'screen_percentage':
        low, high = SCREEN_PERCENTAGE_RANGE
        return None if _is_int(value) and low <= value <= high else f"draft.{key}: an integer {low}-{high}."
    return None if _is_int(value) and value >= 1 else f"draft.{key}: an integer of at least 1."


def draft_error(draft):
    """FR-SQ-7 shape: null, or an object of the draft keys with allowed values; the first problem."""
    if draft is None:
        return None
    if not isinstance(draft, dict):
        return "draft must be an object or null."
    unknown = sorted(set(draft) - set(DRAFT_KEYS))
    if unknown:
        return f"Unknown draft keys: {', '.join(map(str, unknown))}."
    for key in DRAFT_KEYS:
        if key in draft:
            problem = _draft_value_error(key, draft[key])
            if problem:
                return problem
    return None


def _selection_errors(data, errors):
    has_range = 'frame_start' in data or 'frame_end' in data
    if data.get('shots') is not None and has_range:
        errors['shots'] = ONE_SELECTION
    elif has_range and ('frame_start' not in data or 'frame_end' not in data):
        errors['frame_start'] = RANGE_PAIR
    elif has_range and data['frame_start'] > data['frame_end']:
        errors['frame_start'] = RANGE_ORDER
    shots = data.get('shots')
    if shots is not None and len(set(shots)) != len(shots):
        errors.setdefault('shots', "Shot names must be distinct.")


def request_errors(data):
    """The rules on the request body alone (FR-SQ-3, FR-SQ-5..8); ``{field: message}``."""
    errors = {}
    _selection_errors(data, errors)
    if ('resolution_x' in data) != ('resolution_y' in data):
        errors['resolution_x'] = RESOLUTION_PAIR
    video = data.get('video_settings')
    if data.get('frame_format') == FrameFormat.EXR and video is not None:
        errors['video_settings'] = (
            "Video output is not available for EXR format. Use PNG, JPEG, TIFF, BMP, or Targa."
        )
    if data.get('merge_sound') and not (data.get('sound') and video is not None):
        errors['merge_sound'] = MERGE_NEEDS
    problem = draft_error(data.get('draft'))
    if problem:
        errors['draft'] = problem
    return errors


def _rate_errors(facts, rate, errors):
    custom = facts.get('custom_frame_rate')
    if custom and custom[0] * rate[1] != custom[1] * rate[0]:
        errors['preset'] = (
            f"The preset renders at {custom[0]}/{custom[1]} fps but the sequence runs at {rate[0]}/{rate[1]} fps; "
            "turn off the preset's custom frame rate or change the sequence's display rate."
        )


def _selection_fact_errors(data, discovery, errors):
    shots = data.get('shots')
    if shots is not None:
        known = {shot['name'] for shot in discovery.shots}
        unknown = [name for name in shots if name not in known]
        if unknown:
            errors['shots'] = f"Unknown shots: {', '.join(unknown[:20])}."
    if data.get('frame_start') is not None:
        first, last = discovery.playback_start, discovery.playback_end - 1
        if data['frame_start'] < first or data['frame_end'] > last:
            errors['frame_start'] = f"The frame range is outside the sequence ({first}-{last})."


def _output_fact_errors(data, facts, errors):
    if data.get('frame_format') == FrameFormat.EXR and not facts.get('can_exr'):
        errors['frame_format'] = NO_EXR
    if data.get('sound') and not facts.get('can_sound'):
        errors['sound'] = NO_SOUND
    if data.get('video_settings') is not None:
        size = effective_size(data, facts)
        if size is None:
            errors['resolution_x'] = NO_SIZE
        elif size[0] % 2 or size[1] % 2:
            errors['resolution_x'] = f"Video needs an even width and height; the output is {size[0]}x{size[1]}."
    draft = data.get('draft') or {}
    for key, fact in (('temporal_samples', 'temporal_samples'), ('spatial_samples', 'spatial_samples')):
        if key in draft and draft[key] > (facts.get(fact) or 1):
            errors['draft'] = f"draft.{key}: at most the preset's {facts.get(fact) or 1}."


def effective_size(data, facts):
    """The output size: the submission's resolution, else the preset's, else ``None``."""
    if data.get('resolution_x') is not None:
        return [data['resolution_x'], data['resolution_y']]
    size = facts.get('resolution')
    return list(size) if size else None


def facts_errors(data, discovery):
    """The rules that need the DONE ``discovery`` (FR-SQ-3, FR-SQ-5..8, D5b); ``{field: message}``."""
    errors = {}
    facts = discovery.preset_facts or {}
    _rate_errors(facts, [discovery.frame_rate_num, discovery.frame_rate_den], errors)
    _selection_fact_errors(data, discovery, errors)
    _output_fact_errors(data, facts, errors)
    return errors


def facts_warnings(discovery):
    """D5b: a preset frame step above 1 or handle frames make a submission warning."""
    facts = discovery.preset_facts or {}
    if (facts.get('frame_step') or 1) > 1 or (facts.get('handle_frames') or 0) > 0:
        return [STEP_OR_HANDLES]
    return []


def submission_values(submission):
    """The rule inputs of a stored submission, in the request's field names."""
    data = {
        'frame_format': submission.frame_format, 'video_settings': submission.video_settings,
        'sound': submission.sound, 'draft': submission.draft, 'shots': submission.selected_shots,
    }
    if submission.frame_start is not None:
        data.update(frame_start=submission.frame_start, frame_end=submission.frame_end)
    if submission.resolution_x is not None:
        data.update(resolution_x=submission.resolution_x, resolution_y=submission.resolution_y)
    return data
