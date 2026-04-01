# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Schema migration (step 1 of 3): Add SupportedBlenderVersion model and
    nullable FK columns on Project, Job, TiledJob, and Animation.
    """

    dependencies = [
        ('workers', '0006_worker_user'),
    ]

    operations = [
        # Create the SupportedBlenderVersion model
        migrations.CreateModel(
            name='SupportedBlenderVersion',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                (
                    'major',
                    models.IntegerField(
                        help_text='Major version number, derived from series.',
                    ),
                ),
                (
                    'minor',
                    models.IntegerField(
                        help_text='Minor version number, derived from series.',
                    ),
                ),
                (
                    'series',
                    models.CharField(
                        help_text="Major.minor series string, e.g. '4.2' or '5.0'.",
                        max_length=10,
                        unique=True,
                    ),
                ),
                (
                    'resolved_version',
                    models.CharField(
                        help_text="Latest known patch for this series, e.g. '4.2.19'.",
                        max_length=20,
                    ),
                ),
                (
                    'is_default',
                    models.BooleanField(
                        default=False,
                        help_text='Exactly one row must be the default version.',
                    ),
                ),
                (
                    'added_at',
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    'last_patch_check',
                    models.DateTimeField(blank=True, null=True),
                ),
            ],
            options={
                'verbose_name': 'Supported Blender Version',
                'verbose_name_plural': 'Supported Blender Versions',
                'ordering': ['major', 'minor'],
            },
        ),

        # Rename old CharField columns so we can add new FK columns
        migrations.RenameField(
            model_name='job',
            old_name='blender_version',
            new_name='blender_version_old',
        ),
        migrations.RenameField(
            model_name='tiledjob',
            old_name='blender_version',
            new_name='blender_version_old',
        ),
        migrations.RenameField(
            model_name='animation',
            old_name='blender_version',
            new_name='blender_version_old',
        ),

        # Add nullable FK on Project
        migrations.AddField(
            model_name='project',
            name='blender_version',
            field=models.ForeignKey(
                help_text='The Blender version series used for all jobs in this project.',
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='projects',
                to='workers.supportedblenderversion',
            ),
        ),

        # Add nullable FK on Job
        migrations.AddField(
            model_name='job',
            name='blender_version',
            field=models.ForeignKey(
                blank=True,
                help_text='Optional version override; inherits from project when null.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='jobs',
                to='workers.supportedblenderversion',
            ),
        ),

        # Add nullable FK on TiledJob
        migrations.AddField(
            model_name='tiledjob',
            name='blender_version',
            field=models.ForeignKey(
                blank=True,
                help_text='Optional version override; inherits from project when null.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='tiled_jobs',
                to='workers.supportedblenderversion',
            ),
        ),

        # Add nullable FK on Animation
        migrations.AddField(
            model_name='animation',
            name='blender_version',
            field=models.ForeignKey(
                blank=True,
                help_text='Optional version override; inherits from project when null.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='animations',
                to='workers.supportedblenderversion',
            ),
        ),
    ]
