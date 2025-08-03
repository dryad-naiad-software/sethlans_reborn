# workers/models/upload_paths.py
#
# Copyright (c) 2025 Dryad and Naiad Software LLC
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
#
#
# Created by Mario Estrella on 8/1/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn

import uuid
from pathlib import Path
from django.utils.text import slugify


def asset_upload_path(instance, filename):
    """
    Generates a unique upload path for an asset file using short UUIDs.
    Example: media/assets/<project_short_id>/<asset_short_uuid><ext>
    """
    extension = Path(filename).suffix
    project_short_id = str(instance.project.id)[:8]
    asset_short_uuid = uuid.uuid4().hex[:8]
    return f'assets/{project_short_id}/{asset_short_uuid}{extension}'


def job_output_upload_path(instance, filename):
    """
    Generates an upload path for a Job's output file.
    - For animation frames, it groups them under an animation-specific directory.
    - For standalone jobs, it creates a job-specific directory.
    Example (Animation): media/assets/<proj_id>/outputs/animation_<anim_id>/<filename>
    Example (Standalone): media/assets/<proj_id>/outputs/job_<job_id>/<filename>
    """
    project_short_id = str(instance.asset.project.id)[:8]

    # Check if the job is part of an animation
    if instance.animation:
        # Group by parent animation ID
        job_dir = f"animation_{instance.animation.id}"
    else:
        # Standalone job, group by its own ID
        job_dir = f"job_{instance.id}"

    return f'assets/{project_short_id}/outputs/{job_dir}/{filename}'


def tiled_job_output_upload_path(instance, filename):
    """
    Generates an upload path for a TiledJob's final assembled output inside a job-specific directory.
    Example: media/assets/<project_short_id>/outputs/tiled-job_<tiled_job_short_id>/<filename>
    """
    project_short_id = str(instance.asset.project.id)[:8]
    job_dir = f"tiled-job_{str(instance.id)[:8]}"
    return f'assets/{project_short_id}/outputs/{job_dir}/{filename}'


def animation_frame_output_upload_path(instance, filename):
    """
    Generates an upload path for an assembled AnimationFrame output inside an animation-specific directory.
    Example: media/assets/<proj_short_id>/outputs/animation_<animation_id>/<filename>
    """
    project_short_id = str(instance.animation.project.id)[:8]
    anim_dir = f"animation_{instance.animation.id}"
    return f'assets/{project_short_id}/outputs/{anim_dir}/{filename}'


def thumbnail_upload_path(instance, filename):
    """
    Generates an upload path for a thumbnail, adding a '_thumbnail' suffix.
    Example: media/assets/<project_short_id>/thumbnails/<basename>_thumbnail.png
    """
    extension = Path(filename).suffix or ".png"
    passed_stem = Path(filename).stem
    model_name = instance.__class__.__name__.lower()

    # Resolve project id from common relationships
    project_id = None
    if hasattr(instance, 'project') and instance.project is not None:
        project_id = instance.project.id
    elif hasattr(instance, 'asset') and instance.asset is not None:
        project_id = instance.asset.project.id
    elif hasattr(instance, 'animation') and instance.animation is not None:
        project_id = instance.animation.project.id

    project_short_id = str(project_id)[:8] if project_id else "unknown_project"

    # Determine the base name before adding the suffix
    base_stem = ""
    if model_name == 'animation':
        pk_value = getattr(instance, 'pk', getattr(instance, 'id', None))
        # For animations, the signal handler provides a deterministic stem (the PK)
        base_stem = passed_stem or (str(pk_value) if pk_value is not None else "temp")
    else:
        pk_value = getattr(instance, 'id', 'unknown')
        if isinstance(pk_value, uuid.UUID):
            pk_value = str(pk_value)[:8]
        base_stem = f'{model_name}_{pk_value}'

    # Add suffix and build final path
    thumbnail_stem = f"{base_stem}_thumbnail"
    basename = f"{thumbnail_stem}{extension}"
    return f'assets/{project_short_id}/thumbnails/{basename}'