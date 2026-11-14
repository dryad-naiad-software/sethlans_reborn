# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
One record per Unreal pilot attempt (Unreal foundation FR-PM-1, D2).

A record is keyed by ``(asset, overrides_sha256, target_sha256)``: the
asset (which carries the archive pins), the project's CVar-override hash
when the pilot was created, and the hash of the target (engine build,
map, sequence, preset).  Its results stay null until the pilot's worker
reports them through ``POST /api/jobs/{id}/pilot-result/``.

Invariant: ``project_id == asset.project_id`` (an Unreal asset cannot
move to another project, FR-MR-11).  Rows are created only by
``services/unreal_pilot.py`` and their results written only by the
``pilot-result/`` action; no serializer writes them.  The state of a
record and of a key is derived (``renderers/unreal_measured.py``).
"""

from django.db import models
from django.utils import timezone


class UnrealMeasurement(models.Model):
    """A pilot render's measurement of a project target's peak GPU memory."""

    project = models.ForeignKey(
        'workers.Project', on_delete=models.CASCADE, related_name='unreal_measurements',
    )
    asset = models.ForeignKey(
        'workers.Asset', on_delete=models.CASCADE, related_name='unreal_measurements',
    )
    overrides_sha256 = models.CharField(max_length=64)
    target_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)
    pilot_job = models.OneToOneField(
        'workers.Job', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='unreal_measurement',
    )
    engine_build = models.CharField(max_length=32)
    map = models.CharField(max_length=512)
    sequence = models.CharField(max_length=512)
    preset = models.CharField(max_length=512)
    remeasure = models.BooleanField(
        default=False, help_text="Created by an admin re-measurement of a measured key.",
    )
    # Results: all null until a successful pilot-result report.
    peak_mb = models.IntegerField(null=True, blank=True)
    peak_source = models.CharField(max_length=8, blank=True, default='')
    card_delta_peak_mb = models.IntegerField(null=True, blank=True)
    baseline_used_mb = models.IntegerField(null=True, blank=True)
    baseline_drift_mb = models.IntegerField(null=True, blank=True)
    peak_phase = models.CharField(max_length=8, blank=True, default='')
    max_sample_gap_s = models.FloatField(null=True, blank=True)
    prep_s = models.FloatField(null=True, blank=True)
    warmup_s = models.FloatField(null=True, blank=True)
    warmup_ran = models.BooleanField(null=True, blank=True)
    render_s = models.FloatField(null=True, blank=True)
    frames = models.IntegerField(null=True, blank=True)
    seconds_per_frame = models.FloatField(null=True, blank=True)
    per_shot = models.JSONField(null=True, blank=True)
    worker = models.ForeignKey(
        'workers.Worker', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='unreal_measurements',
    )
    worker_hostname = models.CharField(max_length=255, blank=True, default='')
    gpu_index = models.IntegerField(null=True, blank=True)
    gpu_name = models.CharField(max_length=128, blank=True, default='')
    gpu_total_mb = models.IntegerField(null=True, blank=True)
    csv_local_used_peak_mb = models.IntegerField(null=True, blank=True)
    measured_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"UnrealMeasurement {self.pk} (asset {self.asset_id})"

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(
                fields=['asset', 'overrides_sha256', 'target_sha256', '-created_at', '-id'],
                name='unrealmeas_key_latest_idx',
            ),
        ]
