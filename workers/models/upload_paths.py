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


def asset_upload_path(instance, filename):
    """
    media/assets/<project_id>/<uuid><ext>
    """
    extension = Path(filename).suffix
    return f'assets/{instance.project.id}/{uuid.uuid4()}{extension}'


def job_output_upload_path(instance, filename):
    """
    media/assets/<project_id>/outputs/job_<job_id><ext>
    """
    extension = Path(filename).suffix
    project_id = instance.asset.project.id
    return f'assets/{project_id}/outputs/job_{instance.id}{extension}'


def tiled_job_output_upload_path(instance, filename):
    """
    media/assets/<project_id>/outputs/tiled_<tiled_job_id><ext>
    """
    extension = Path(filename).suffix
    project_id = instance.asset.project.id
    return f'assets/{project_id}/outputs/tiled_{instance.id}{extension}'


def animation_frame_output_upload_path(instance, filename):
    """
    media/assets/<proj_id>/outputs/anim_<anim_id>/frame_<frame_number><ext>
    """
    extension = Path(filename).suffix
    project_id = instance.animation.project.id
    return (
        f'assets/{project_id}/outputs/anim_{instance.animation.id}/'
        f'frame_{instance.frame_number:04d}{extension}'
    )


def thumbnail_upload_path(instance, filename):
    """
    media/assets/<project_id>/thumbnails/<basename>

    - For Animation thumbnails:
        * Respect the passed-in filename (so we can version: '<pk>-0002.png').
        * Default to '{pk}.png' if no usable name is provided.
    - For other models, use '<model>_<id><ext>'.
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

    if not project_id:
        project_id = "unknown_project"

    # Special case: Animation -> respect caller-provided basename; fallback to '{pk}.png'
    if model_name == 'animation':
        pk_value = getattr(instance, 'pk', getattr(instance, 'id', None))
        stem = passed_stem or (str(pk_value) if pk_value is not None else "temp")
        basename = f"{stem}{extension}"
        return f'assets/{project_id}/thumbnails/{basename}'

    # Default behavior for other models
    basename = f'{model_name}_{getattr(instance, "id", "unknown")}{extension}'
    return f'assets/{project_id}/thumbnails/{basename}'
