# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Schema migration (step 3 of 3): Make Project.blender_version non-nullable
    and remove old CharField columns from Job, TiledJob, and Animation.
    """

    dependencies = [
        ('workers', '0008_populate_blender_versions'),
    ]

    operations = [
        # Make Project.blender_version non-nullable
        migrations.AlterField(
            model_name='project',
            name='blender_version',
            field=models.ForeignKey(
                help_text='The Blender version series used for all jobs in this project.',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='projects',
                to='workers.supportedblenderversion',
            ),
        ),

        # Remove old CharField columns
        migrations.RemoveField(
            model_name='job',
            name='blender_version_old',
        ),
        migrations.RemoveField(
            model_name='tiledjob',
            name='blender_version_old',
        ),
        migrations.RemoveField(
            model_name='animation',
            name='blender_version_old',
        ),
    ]
