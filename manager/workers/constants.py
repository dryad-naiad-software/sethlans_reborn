# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Centralized constants for the workers application.

This module defines the API contract for various job settings, including
supported render engines, devices, tiling configurations, and render
setting keys. These constants are used for validation, API documentation,
and ensuring consistency across the entire system.
"""
from django.db import models


class RenderEngine(models.TextChoices):
    """
    Defines the supported Blender render engines.

    The choice values are the exact string Blender's Python API expects
    when setting the render engine (e.g., `bpy.context.scene.render.engine = 'CYCLES'`).
    """
    CYCLES = 'CYCLES', 'Cycles'
    EEVEE = 'BLENDER_EEVEE_NEXT', 'Eevee'
    WORKBENCH = 'WORKBENCH', 'Workbench'


class CyclesFeatureSet(models.TextChoices):
    """
    Defines the supported feature sets for the Cycles render engine.
    """
    SUPPORTED = 'SUPPORTED', 'Supported'
    EXPERIMENTAL = 'EXPERIMENTAL', 'Experimental'


class RenderDevice(models.TextChoices):
    """
    Defines the user's device preference for rendering.

    - `CPU`: Forces rendering on the CPU only.
    - `GPU`: Forces rendering on the GPU only.
    - `ANY`: The worker can choose the most efficient device available.
    """
    CPU = 'CPU', 'CPU Only'
    GPU = 'GPU', 'GPU Only'
    ANY = 'ANY', 'Any Available Device'


class TilingConfiguration(models.TextChoices):
    """
    Defines the supported grid sizes for tiled rendering.
    """
    NONE = 'NONE', 'None'
    TILE_2X2 = '2x2', '2x2 (4 Tiles)'
    TILE_3X3 = '3x3', '3x3 (9 Tiles)'
    TILE_4X4 = '4x4', '4x4 (16 Tiles)'
    TILE_5X5 = '5x5', '5x5 (25 Tiles)'


class OutputFormat(models.TextChoices):
    """
    Defines the supported Blender output image formats.

    The choice values match Blender's ``-F`` flag strings exactly, allowing
    direct pass-through when constructing render commands.
    """
    PNG = 'PNG', 'PNG'
    JPEG = 'JPEG', 'JPEG'
    OPEN_EXR = 'OPEN_EXR', 'OpenEXR'
    OPEN_EXR_MULTILAYER = 'OPEN_EXR_MULTILAYER', 'OpenEXR MultiLayer'
    TIFF = 'TIFF', 'TIFF'
    BMP = 'BMP', 'BMP'
    HDR = 'HDR', 'HDR (Radiance)'
    TARGA = 'TARGA', 'Targa'


# Maps each OutputFormat value to its file extension string.
FORMAT_EXTENSIONS = {
    'PNG': '.png',
    'JPEG': '.jpg',
    'OPEN_EXR': '.exr',
    'OPEN_EXR_MULTILAYER': '.exr',
    'TIFF': '.tif',
    'BMP': '.bmp',
    'HDR': '.hdr',
    'TARGA': '.tga',
}

# The subset of OutputFormat values that Pillow can read and write.
PILLOW_COMPATIBLE_FORMATS = frozenset({
    'PNG', 'JPEG', 'TIFF', 'BMP', 'TARGA',
})

# Maps OutputFormat values to Pillow's ``format`` parameter strings.
PILLOW_FORMAT_NAMES = {
    'PNG': 'PNG',
    'JPEG': 'JPEG',
    'TIFF': 'TIFF',
    'BMP': 'BMP',
    'TARGA': 'TGA',
}


class RenderSettings:
    """
    Defines the string keys for the ``render_settings`` dictionary override.

    All constant values must be valid ``bpy.context.scene.*`` attribute paths
    where all intermediate objects exist in Blender's Python API. For example,
    ``"render.image_settings.file_format"`` maps to
    ``bpy.context.scene.render.image_settings.file_format``.
    """
    # General Settings
    RENDER_ENGINE = "render.engine"
    SAMPLES = "cycles.samples"
    RESOLUTION_X = "render.resolution_x"
    RESOLUTION_Y = "render.resolution_y"
    RESOLUTION_PERCENTAGE = "render.resolution_percentage"

    # Image Output Settings
    IMAGE_FILE_FORMAT = "render.image_settings.file_format"
    IMAGE_QUALITY = "render.image_settings.quality"
    IMAGE_COLOR_DEPTH = "render.image_settings.color_depth"

    # Cycles-specific Settings
    CYCLES_DEVICE = "cycles.device"
    CYCLES_FEATURE_SET = "cycles.feature_set"

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


class WorkerStatus(models.TextChoices):
    """
    Defines the possible operational states of a worker.

    IDLE and RENDERING are reported by the worker agent.
    OFFLINE is derived by the manager when a worker's heartbeat is stale.
    """
    IDLE = 'IDLE', 'Idle'
    RENDERING = 'RENDERING', 'Rendering'
    OFFLINE = 'OFFLINE', 'Offline'


# Workers reporting no heartbeat within this many seconds are considered OFFLINE.
WORKER_STALENESS_SECONDS = 90
