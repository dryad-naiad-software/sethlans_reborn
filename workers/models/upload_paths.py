# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (c) 2025 Dryad and Naiad Software LLC
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

    Args:
        instance (Asset): The asset instance being saved.
        filename (str): The original filename of the uploaded file.

    Returns:
        str: The generated file path.
    """
    extension = Path(filename).suffix
    project_short_id = str(instance.project.id)[:8]
    asset_short_uuid = uuid.uuid4().hex[:8]
    return f'assets/{project_short_id}/{asset_short_uuid}{extension}'


def job_output_upload_path(instance, filename):
    """
    Generates a descriptive upload path for a Job's output file.
    - For animation frames, it groups them under a slugified animation name directory.
    - For standalone jobs, it creates a slugified job name directory.
    Example (Animation): media/assets/<proj_id>/outputs/my-cool-animation-999/<filename>
    Example (Standalone): media/assets/<proj_id>/outputs/my-test-job-123/<filename>

    Args:
        instance (Job): The job instance being saved.
        filename (str): The original filename of the output file.

    Returns:
        str: The generated file path.
    """
    project_short_id = str(instance.asset.project.id)[:8]

    # Check if the job is part of an animation
    if instance.animation:
        # Group by slugified parent animation name and ID
        slug = slugify(instance.animation.name)
        job_dir = f"{slug}-{instance.animation.id}"
    else:
        # Standalone job, group by its own slugified name and ID
        slug = slugify(instance.name)
        job_dir = f"{slug}-{instance.id}"

    return f'assets/{project_short_id}/outputs/{job_dir}/{filename}'


def tiled_job_output_upload_path(instance, filename):
    """
    Generates a descriptive upload path for a TiledJob's final assembled output.
    Example: media/assets/<proj_id>/outputs/my-tiled-render-abc12345/<filename>

    Args:
        instance (TiledJob): The TiledJob instance being saved.
        filename (str): The original filename of the output file.

    Returns:
        str: The generated file path.
    """
    project_short_id = str(instance.project.id)[:8]
    slug = slugify(instance.name)
    job_dir = f"{slug}-{str(instance.id)[:8]}"
    return f'assets/{project_short_id}/outputs/{job_dir}/{filename}'


def animation_frame_output_upload_path(instance, filename):
    """
    Generates a descriptive upload path for an assembled AnimationFrame output.
    Example: media/assets/<proj_id>/outputs/my-cool-animation-456/<filename>

    Args:
        instance (AnimationFrame): The AnimationFrame instance being saved.
        filename (str): The original filename of the output file.

    Returns:
        str: The generated file path.
    """
    project_short_id = str(instance.animation.project.id)[:8]
    slug = slugify(instance.animation.name)
    anim_dir = f"{slug}-{instance.animation.id}"
    return f'assets/{project_short_id}/outputs/{anim_dir}/{filename}'


def thumbnail_upload_path(instance, filename):
    """
    Generates a descriptive upload path for a thumbnail.
    The final name is a slug of the instance's name, its primary key, and a suffix.
    Example: media/assets/<proj_id>/thumbnails/my-cool-job-123_thumbnail.png

    Args:
        instance (Model): The model instance being saved.
        filename (str): The original filename of the thumbnail (used for extension).

    Returns:
        str: The generated file path.
    """
    from workers.models import AnimationFrame

    extension = Path(filename).suffix or ".png"
    base_stem = "unknown-thumbnail"
    project_id = None

    # Resolve name, pk, and project_id based on instance type
    if hasattr(instance, 'project'):
        project_id = instance.project_id
    elif hasattr(instance, 'asset'):
        project_id = instance.asset.project_id
    elif hasattr(instance, 'animation'):
        project_id = instance.animation.project_id

    if isinstance(instance, AnimationFrame):
        name = instance.animation.name
        pk = instance.animation.id
        # Add frame number for clarity, though parent animation ID provides uniqueness
        base_stem = f"{slugify(name)}-{pk}-frame-{instance.frame_number}"
    elif hasattr(instance, 'name') and hasattr(instance, 'pk'):
        name = instance.name
        pk = instance.pk
        if isinstance(pk, uuid.UUID):
            pk = str(pk)[:8]
        base_stem = f"{slugify(name)}-{pk}"

    project_short_id = str(project_id)[:8] if project_id else "unknown_project"
    final_filename = f"{base_stem}_thumbnail{extension}"

    return f'assets/{project_short_id}/thumbnails/{final_filename}'