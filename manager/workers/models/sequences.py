# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal sequence rendering records (spec FR-SD-1, FR-SQ-1).

``SequenceDiscovery`` holds what a ``shots`` job read from one sequence:
its playback range, display rate, shots and the preset's facts.  It is
keyed by (asset, engine build, map, sequence, preset) and reused by later
submissions of the same key (D1, FR-SD-2); ``target_sha256`` holds the
frozen hash of the last four (``shared.unreal.validation.target_sha256``).  Rows are created and written
only by ``services/sequence_discovery.py``; no serializer writes them.

Invariant: ``project_id == asset.project_id`` (an Unreal asset cannot move
to another project).  Shot indexes are 1-based; ``end`` values are
exclusive.  Shot names were cleaned by the worker and again by the manager
(FR-SD-6) and are never used in a storage path.
"""

from django.db import models
from django.utils import timezone

from ..constants_sequence import (
    SHOT_SOURCE_CHOICES, SUBMISSION_NAME_MAX, DiscoveryStatus, FrameFormat, SequenceStatus,
)
from ..constants_video import VideoStatus
from .upload_paths_sequence import submission_file_upload_path


class SequenceDiscovery(models.Model):
    """One reading of a sequence's shots (FR-SD-1)."""

    project = models.ForeignKey(
        'workers.Project', on_delete=models.CASCADE, related_name='sequence_discoveries',
    )
    asset = models.ForeignKey(
        'workers.Asset', on_delete=models.CASCADE, related_name='sequence_discoveries',
    )
    engine_build = models.CharField(max_length=32)
    map = models.CharField(max_length=512)
    sequence = models.CharField(max_length=512)
    preset = models.CharField(max_length=512)
    target_sha256 = models.CharField(
        max_length=64, editable=False,
        help_text="target_sha256(engine_build, map, sequence, preset): the indexed key.",
    )
    status = models.CharField(
        max_length=8, choices=DiscoveryStatus.choices, default=DiscoveryStatus.QUEUED,
    )
    job = models.OneToOneField(
        'workers.Job', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sequence_discovery',
        help_text="The manager-created 'shots' job that reads the shots.",
    )
    created_at = models.DateTimeField(default=timezone.now)
    playback_start = models.IntegerField(null=True, blank=True)
    playback_end = models.IntegerField(
        null=True, blank=True, help_text="Exclusive.",
    )
    frame_rate_num = models.PositiveIntegerField(null=True, blank=True)
    frame_rate_den = models.PositiveIntegerField(null=True, blank=True)
    shot_source = models.CharField(max_length=16, choices=SHOT_SOURCE_CHOICES, blank=True, default='')
    shots = models.JSONField(
        default=list, blank=True,
        help_text='[{"index", "name", "start", "end"}]: 1-based index, end exclusive.',
    )
    preset_facts = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    error_message = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = "Sequence discovery"
        verbose_name_plural = "Sequence discoveries"
        # FR-SD-1's key index.  The three object paths (512 characters each)
        # are replaced by their frozen hash: MySQL caps an index key at
        # 3,072 bytes, which the paths alone exceed.
        indexes = [
            models.Index(fields=['asset', 'target_sha256', '-created_at', '-id'], name='seqdisc_key_idx'),
        ]

    def __str__(self):
        return f"Discovery {self.pk} of {self.sequence} ({self.status})"


def _status(choices, **kwargs):
    return models.CharField(max_length=12, choices=choices, **kwargs)


def _item_status():
    """A result item's state (FR-SV-2): ``VideoStatus``, or null for "not asked for"."""
    return models.CharField(max_length=12, choices=VideoStatus.choices, null=True, blank=True)


def _result_file():
    return models.FileField(upload_to=submission_file_upload_path, null=True, blank=True, max_length=512)


class SequenceSubmission(models.Model):
    """One version of a sequence render (FR-SQ-1).

    Created by ``services/sequence_submit.py``; planned (shots, pieces,
    pilot) by ``services/sequence_create.py``; its status follows its
    shots and jobs through ``services/sequence_status.refresh_submission``.
    Immutable through the API (FR-SQ-10).  ``frame_start``/``frame_end``
    are inclusive edit frames; ``selected_shots`` holds shot names.
    """

    project = models.ForeignKey('workers.Project', on_delete=models.CASCADE, related_name='sequence_submissions')
    asset = models.ForeignKey('workers.Asset', on_delete=models.CASCADE, related_name='sequence_submissions')
    discovery = models.ForeignKey(SequenceDiscovery, on_delete=models.RESTRICT, related_name='submissions')
    engine_build = models.CharField(max_length=32)
    map = models.CharField(max_length=512)
    sequence = models.CharField(max_length=512)
    preset = models.CharField(max_length=512)
    version = models.PositiveIntegerField()
    name = models.CharField(max_length=SUBMISSION_NAME_MAX)
    # Selection (FR-SQ-3): none, shot names, or an inclusive frame range.
    selected_shots = models.JSONField(null=True, blank=True)
    frame_start = models.IntegerField(null=True, blank=True)
    frame_end = models.IntegerField(null=True, blank=True)
    # Output and draft (FR-SQ-5..9); null resolution means the preset's size.
    resolution_x = models.PositiveIntegerField(null=True, blank=True)
    resolution_y = models.PositiveIntegerField(null=True, blank=True)
    frame_format = models.CharField(max_length=4, choices=FrameFormat.choices, default=FrameFormat.PNG)
    video_settings = models.JSONField(null=True, blank=True)
    sound = models.BooleanField(default=True)
    merge_sound = models.BooleanField(default=False)
    draft = models.JSONField(null=True, blank=True)
    # Project settings copied at create (D18): the preset's override entries,
    # resolved to ``sound_offset_ms`` for the sequence's rate once it is known.
    piece_seconds = models.PositiveSmallIntegerField()
    lead_in_ms = models.PositiveIntegerField()
    sound_offsets = models.JSONField(default=dict, blank=True)
    sound_offset_ms = models.IntegerField(null=True, blank=True)
    # Planning (FR-SR-1, D8): the key's shot set and the pilot's frames.
    shot_set_sha256 = models.CharField(max_length=64, blank=True, default='')
    pilot_frames = models.JSONField(default=list, blank=True)
    status = _status(SequenceStatus.choices, default=SequenceStatus.DISCOVERING)
    cancel_requested = models.BooleanField(default=False)
    assembly_boot = models.CharField(max_length=36, blank=True, default='')
    error_message = models.TextField(blank=True, default='')
    warnings = models.JSONField(default=list, blank=True)
    submitted_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    total_render_time_seconds = models.IntegerField(default=0)
    # Whole-submission results (FR-SV-1, FR-SV-2) and sound runs (FR-SA-11).
    video_status = _item_status()
    video_file = _result_file()
    video_error = models.TextField(blank=True, default='')
    sound_status = _item_status()
    sound_file = _result_file()
    sound_video_status = _item_status()
    sound_video_file = _result_file()
    sound_runs = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-submitted_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['project', 'sequence', 'version'], name='seqsub_unique_version'),
        ]
        indexes = [models.Index(fields=['project', '-submitted_at'], name='seqsub_project_idx')]

    def __str__(self):
        return f"{self.name} ({self.status})"
