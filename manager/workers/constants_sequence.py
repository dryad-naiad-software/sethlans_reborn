# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Constants of Unreal sequence rendering (spec FR-SD-1, FR-SQ, FR-SP, FR-SS,
D8, D18, D20): statuses, the manager-only ``render_settings`` keys, limits,
the reserved job-name prefix and the manager-made job names.

No model import here: ``models/jobs.py`` reads the key names.
"""

from django.db import models

from shared.unreal.shots_result import SHOT_SOURCES
from shared.unreal.sound_result import RUN_SHOTS_KEY


class DiscoveryStatus(models.TextChoices):
    """A ``SequenceDiscovery``'s state (FR-SD-1)."""
    QUEUED = 'QUEUED', 'Queued'
    RUNNING = 'RUNNING', 'Running'
    DONE = 'DONE', 'Done'
    ERROR = 'ERROR', 'Error'


class SequenceStatus(models.TextChoices):
    """A ``SequenceSubmission``'s state (FR-SQ-1, FR-SS-3)."""
    DISCOVERING = 'DISCOVERING', 'Reading shots'
    QUEUED = 'QUEUED', 'Queued'
    RENDERING = 'RENDERING', 'Rendering'
    ASSEMBLING = 'ASSEMBLING', 'Assembling'
    DONE = 'DONE', 'Done'
    PARTLY_DONE = 'PARTLY_DONE', 'Partly done'
    ERROR = 'ERROR', 'Error'
    CANCELED = 'CANCELED', 'Canceled'


class ShotStatus(models.TextChoices):
    """A ``SequenceShot``'s state (FR-SS-1)."""
    PENDING = 'PENDING', 'Pending'
    RENDERING = 'RENDERING', 'Rendering'
    DONE = 'DONE', 'Done'
    ERROR = 'ERROR', 'Error'
    CANCELED = 'CANCELED', 'Canceled'


class FrameFormat(models.TextChoices):
    """A submission's frame format (FR-SQ-6)."""
    PNG = 'PNG', 'PNG'
    EXR = 'EXR', 'EXR'


FRAME_EXTENSIONS = {FrameFormat.PNG: 'png', FrameFormat.EXR: 'exr'}

# A discovery that still waits for its ``shots`` job.
OPEN_DISCOVERY_STATUSES = (DiscoveryStatus.QUEUED, DiscoveryStatus.RUNNING)
# FR-SD-2: the latest record of a key in one of these is reused.
REUSABLE_DISCOVERY_STATUSES = OPEN_DISCOVERY_STATUSES + (DiscoveryStatus.DONE,)
FINAL_SEQUENCE_STATUSES = (
    SequenceStatus.DONE, SequenceStatus.PARTLY_DONE, SequenceStatus.ERROR, SequenceStatus.CANCELED,
)

SHOT_SOURCE_CHOICES = tuple((value, value) for value in SHOT_SOURCES)

# Manager-only ``render_settings`` keys of pieces and pilots (D8, FR-SW-2).
RESOLUTION_KEY = 'unreal.resolution'
SHOT_SET_KEY = 'unreal.shot_set_sha256'
# One key for a piece's and an audio job's shots; the worker reads it from the shared module.
PIECE_SHOTS_KEY = RUN_SHOTS_KEY
OUTPUT_SIZE_KEY = 'unreal.output_size'
PILOT_FRAMES_KEY = 'unreal.pilot_frames'
DRAFT_KEY = 'unreal.draft'
# A pilot's card kind (per-machine memory test D4, FR-MT-R-5).
CARD_KIND_KEY = 'unreal.card_kind'
# Clients never send these (FR-SW-2); ``unreal.resolution`` is open to standalone jobs.
MANAGER_ONLY_KEYS = (SHOT_SET_KEY, PIECE_SHOTS_KEY, OUTPUT_SIZE_KEY, PILOT_FRAMES_KEY, DRAFT_KEY, CARD_KIND_KEY)

# Limits (FR-SQ-3, FR-SQ-5, FR-SP-7, FR-SR-1, FR-SS-1, D18).
MAX_SUBMISSION_SHOTS = 2_000
MAX_PIECES = 10_000
MAX_PILOT_FRAMES = 256
MAX_TIMINGS = 50
MAX_SUBMISSION_WARNINGS = 64
RESOLUTION_MIN = 64
RESOLUTION_MAX = 16_384
PIECE_SECONDS_MIN, PIECE_SECONDS_MAX, PIECE_SECONDS_DEFAULT = 5, 600, 30
LEAD_IN_MS_MAX, LEAD_IN_MS_DEFAULT = 10_000, 1_000
SOUND_OFFSET_MS_MAX = 10_000
MAX_SOUND_OFFSETS = 64
SUBMISSION_NAME_MAX = 80

# D20: names of manager-created sequence jobs; reserved as ``sethlans-pilot-`` is.
SEQUENCE_NAME_PREFIX = 'sethlans-seq-'
SHOTS_JOB_PREFIX = f'{SEQUENCE_NAME_PREFIX}shots-'

# FR-SD-9: why a discovery failed when its ``shots`` job ended without a result.
SHOTS_JOB_CANCELED = "The shots job was canceled."
SHOTS_JOB_DELETED = "The shots job was deleted."
SHOTS_JOB_DONE_WITHOUT_RESULT = "The shots job finished without a result."
SHOTS_JOB_FAILED = "The shots job failed."

# FR-SA-11, D24: where a run's applied correction came from; the form flag's values (FR-SA-6).
SOUND_APPLIED_SOURCES = ('measured', 'override', 'none')
SOUND_FLAG_VALUES = ('true', 'false')

# FR-SA-8: a failed audio job's sound items; the prefix marks an item the sound render failed.
SOUND_RENDER_PREFIX = "The sound render "
SOUND_RENDER_FAILED = SOUND_RENDER_PREFIX + "failed: {reason}."
SOUND_RENDER_CANCELED = SOUND_RENDER_PREFIX + "was canceled."


def shots_job_name(discovery_id):
    """D20: ``sethlans-seq-shots-<discovery id>`` (at most 40 characters)."""
    return f'{SHOTS_JOB_PREFIX}{discovery_id}'


def piece_job_name(submission_id, number):
    """D20: ``sethlans-seq-<submission id>-p<n>``, ``n`` from 1."""
    return f'{SEQUENCE_NAME_PREFIX}{submission_id}-p{number}'


def audio_job_name(submission_id, number):
    """D20: ``sethlans-seq-<submission id>-a<n>``, ``n`` from 1."""
    return f'{SEQUENCE_NAME_PREFIX}{submission_id}-a{number}'
