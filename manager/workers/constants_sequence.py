# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Constants of Unreal sequence rendering (spec FR-SD-1, D20): statuses,
the reserved job-name prefix and the manager-made job names.
"""

from django.db import models

from shared.unreal.shots_result import SHOT_SOURCES


class DiscoveryStatus(models.TextChoices):
    """A ``SequenceDiscovery``'s state (FR-SD-1)."""
    QUEUED = 'QUEUED', 'Queued'
    RUNNING = 'RUNNING', 'Running'
    DONE = 'DONE', 'Done'
    ERROR = 'ERROR', 'Error'


# A discovery that still waits for its ``shots`` job.
OPEN_DISCOVERY_STATUSES = (DiscoveryStatus.QUEUED, DiscoveryStatus.RUNNING)
# FR-SD-2: the latest record of a key in one of these is reused.
REUSABLE_DISCOVERY_STATUSES = OPEN_DISCOVERY_STATUSES + (DiscoveryStatus.DONE,)

SHOT_SOURCE_CHOICES = tuple((value, value) for value in SHOT_SOURCES)

# D20: names of manager-created sequence jobs; reserved as ``sethlans-pilot-`` is.
SEQUENCE_NAME_PREFIX = 'sethlans-seq-'
SHOTS_JOB_PREFIX = f'{SEQUENCE_NAME_PREFIX}shots-'

# FR-SD-9: why a discovery failed when its ``shots`` job ended without a result.
SHOTS_JOB_CANCELED = "The shots job was canceled."
SHOTS_JOB_DELETED = "The shots job was deleted."
SHOTS_JOB_DONE_WITHOUT_RESULT = "The shots job finished without a result."
SHOTS_JOB_FAILED = "The shots job failed."


def shots_job_name(discovery_id):
    """D20: ``sethlans-seq-shots-<discovery id>`` (at most 40 characters)."""
    return f'{SHOTS_JOB_PREFIX}{discovery_id}'
