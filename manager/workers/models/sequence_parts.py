# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Shots and frames of an Unreal sequence submission (spec FR-SS-1, FR-SS-2,
FR-SV-2).

A ``SequenceShot`` is one chosen shot part: its discovery index (1-based,
unique per submission), its cleaned name and its frames ``[start_frame,
end_frame)`` (end exclusive), clipped to the submission's frame range.
Its status follows the piece jobs that cover it
(``services/sequence_status.py``); writes there use ``update()``.

A ``SequenceFrame`` is one kept frame uploaded by a piece.  A repeat upload
of the same (shot, frame) replaces the file (``services/sequence_frames.py``);
deleting the job keeps the frame (``SET_NULL``).
"""

from django.db import models

from ..constants_sequence import ShotStatus
from ..constants_video import VideoStatus
from .upload_paths_sequence import frame_upload_path, shot_file_upload_path


def _item_status():
    return models.CharField(max_length=12, choices=VideoStatus.choices, null=True, blank=True)


def _shot_file():
    return models.FileField(upload_to=shot_file_upload_path, null=True, blank=True, max_length=512)


class SequenceShot(models.Model):
    """One chosen shot part of a submission (FR-SS-1)."""

    submission = models.ForeignKey('workers.SequenceSubmission', on_delete=models.CASCADE, related_name='shots')
    index = models.PositiveIntegerField(help_text="The discovery's 1-based shot index.")
    name = models.CharField(max_length=64)
    start_frame = models.IntegerField()
    end_frame = models.IntegerField(help_text="Exclusive.")
    status = models.CharField(max_length=12, choices=ShotStatus.choices, default=ShotStatus.PENDING)
    error_message = models.TextField(blank=True, default='')
    render_time_seconds = models.IntegerField(default=0)
    timings = models.JSONField(default=list, blank=True, help_text="R7 timings, at most 50, newest last.")
    video_status = _item_status()
    video_file = _shot_file()
    video_error = models.TextField(blank=True, default='')
    sound_status = _item_status()
    sound_file = _shot_file()
    sound_error = models.TextField(blank=True, default='')
    sound_video_status = _item_status()
    sound_video_file = _shot_file()
    sound_video_error = models.TextField(blank=True, default='')
    # D16: the boot id of the serving process whose task last claimed one of this shot's items.
    assembly_boot = models.CharField(max_length=36, blank=True, default='')

    class Meta:
        ordering = ['submission', 'index']
        constraints = [
            models.UniqueConstraint(fields=['submission', 'index'], name='seqshot_unique_index'),
        ]
        indexes = [models.Index(fields=['submission', 'start_frame'], name='seqshot_start_idx')]

    def __str__(self):
        return f"Shot {self.index} {self.name} ({self.status})"


class SequenceFrame(models.Model):
    """One kept frame of a shot (FR-SS-2)."""

    shot = models.ForeignKey(SequenceShot, on_delete=models.CASCADE, related_name='frames')
    job = models.ForeignKey(
        'workers.Job', on_delete=models.SET_NULL, null=True, blank=True, related_name='sequence_frames',
    )
    frame_number = models.IntegerField()
    file = models.FileField(upload_to=frame_upload_path, max_length=512)

    class Meta:
        ordering = ['shot', 'frame_number']
        constraints = [
            models.UniqueConstraint(fields=['shot', 'frame_number'], name='seqframe_unique_frame'),
        ]

    def __str__(self):
        return f"Frame {self.frame_number} of shot {self.shot_id}"
