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
# Created by Mario Estrella on 07/27/2025.
# Dryad and Naiad Software LLC
# mestrella@dryadandnaiad.com
# Project: sethlans_reborn
#

"""
Constants for the workers application, defining the API contract for job settings.
"""
from django.db import models


class TilingConfiguration(models.TextChoices):
    """
    Defines the supported grid sizes for tiled rendering.
    """
    NONE = 'NONE', 'None'
    TILE_2X2 = '2x2', '2x2 (4 Tiles)'
    TILE_3X3 = '3x3', '3x3 (9 Tiles)'
    TILE_4X4 = '4x4', '4x4 (16 Tiles)'
    TILE_5X5 = '5x5', '5x5 (25 Tiles)'


class SupportedBlenderVersions(models.TextChoices):
    """
    Defines the officially supported Blender versions (latest patch of each series).
    This can be used to populate UI dropdowns and for API validation.
    """
    V4_5_LTS = "4.5.0", "Blender 4.5.1 (LTS)"
    V4_4 = "4.4.3", "Blender 4.4.3"
    V4_3 = "4.3.2", "Blender 4.3.2"
    V4_2_LTS = "4.2.12", "Blender 4.2.12 (LTS)"
    V4_1 = "4.1.1", "Blender 4.1.1"
    V4_0 = "4.0.2", "Blender 4.0.2"


class RenderSettings:
    """
    Defines the string keys for the render_settings dictionary override.
    These are used by the worker to apply settings via Python expressions.
    The keys must be the full property path from 'bpy.context.scene'.
    """
    # General Settings
    SAMPLES = "cycles.samples"
    RESOLUTION_X = "render.resolution_x"
    RESOLUTION_Y = "render.resolution_y"
    RESOLUTION_PERCENTAGE = "render.resolution_percentage"

    # Tiled/Border Rendering Settings
    USE_BORDER = "render.use_border"
    CROP_TO_BORDER = "render.use_crop_to_border"
    BORDER_MIN_X = "render.border_min_x"
    BORDER_MAX_X = "render.border_max_x"
    BORDER_MIN_Y = "render.border_min_y"
    BORDER_MAX_Y = "render.border_max_y"

    # Performance Settings
    TILE_X = "render.tile_x"
    TILE_Y = "render.tile_y"