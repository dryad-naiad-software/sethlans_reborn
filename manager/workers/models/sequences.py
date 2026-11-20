# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Unreal sequence rendering records (spec FR-SD-1).

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

from ..constants_sequence import SHOT_SOURCE_CHOICES, DiscoveryStatus


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
