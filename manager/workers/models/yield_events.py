# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
WorkerYieldEvent model — records when a worker yields a render job.

Yield events are normal operational events (artist returned to their
workstation, schedule window closed, etc.), not failure signals.
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from ..constants import GraceOutcome, YieldReason
from .workers import Worker


class WorkerYieldEvent(models.Model):
    """Records a single yield event from a worker agent."""

    worker = models.ForeignKey(
        Worker,
        on_delete=models.CASCADE,
        related_name='yield_events',
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    reason = models.CharField(
        max_length=50,
        choices=YieldReason.choices,
    )
    grace_outcome = models.CharField(
        max_length=20,
        choices=GraceOutcome.choices,
    )
    progress_at_yield = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
    )
    job = models.ForeignKey(
        'workers.Job',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    job_name = models.CharField(
        max_length=40,
        blank=True,
        default='',
        help_text=(
            "Denormalized job name for display after job deletion."
        ),
    )

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['worker', '-timestamp']),
        ]

    def __str__(self):
        return (
            f"YieldEvent({self.worker}, {self.reason}, "
            f"{self.timestamp})"
        )
