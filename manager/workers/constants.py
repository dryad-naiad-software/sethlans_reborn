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
from shared.unreal.render_context import GAME_OVERRIDES_VALUES as UNREAL_GAME_OVERRIDES  # FR-GC-2, FR-GC-7
from shared.unreal.render_context import TEXTURE_STREAMING_VALUES as UNREAL_TEXTURE_STREAMING

# Video assembly constants live in constants_video; re-exported for importers.
from .constants_video import (  # noqa: F401
    CONTAINER_CONTENT_TYPES, VIDEO_CODEC_CONTAINER_MAP, VIDEO_CODECS,
    VIDEO_COMPATIBLE_FORMATS, VIDEO_CONTAINERS, VIDEO_PRESETS, VideoStatus,
)


class Renderer(models.TextChoices):
    """The render applications a project (and its jobs) can target."""
    BLENDER = 'BLENDER', 'Blender'
    UNREAL = 'UNREAL', 'Unreal Engine'


class AssetSourceType(models.TextChoices):
    """How an asset's scene data reaches the workers."""
    BLEND_FILE = 'blend_file', 'Uploaded .blend file'
    SHARED_PROJECT = 'shared_project', 'Project archive on a share'


# Output formats Unreal jobs may request in phase 1 (spec D5: PNG only).
UNREAL_OUTPUT_FORMATS = ('PNG',)


class UnrealKind(models.TextChoices):
    """A job's role in the Unreal measured-peak gate (spec FR-PM-1; sequence D3)."""
    RENDER = 'render', 'Render'
    PILOT = 'pilot', 'Pilot'
    AUDIO = 'audio', 'Audio'
    SHOTS = 'shots', 'Shots'


UNREAL_GAME_OVERRIDES_CHOICES = tuple((value, value) for value in UNREAL_GAME_OVERRIDES)
UNREAL_TEXTURE_STREAMING_CHOICES = tuple((value, value) for value in UNREAL_TEXTURE_STREAMING)

# Job names reserved for manager-created pilot jobs (FR-PM-9).
PILOT_NAME_PREFIX = 'sethlans-pilot-'
# A pilot job's target fields: its measurement record describes what it renders (FR-PM-9).
PILOT_TARGET_FIELDS = ('render_settings', 'start_frame', 'end_frame', 'asset', 'engine_build')


class RenderEngine(models.TextChoices):
    """
    Defines the supported Blender render engines.

    The choice values are the product's own engine names, stored with every
    job. They are not always the identifier Blender accepts: the worker
    translates them for the job's Blender version when it sets
    `scene.render.engine` (worker `engine_script.py`, #242).
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

# The output format a job uses when its render_settings name none. Used by
# the render_settings validation (serializers/validation_utils.py). The
# serializers for jobs and animations, the tiled-job and animation views,
# image_assembler.py, video_assembler.py and the worker's ``-F`` flag apply
# the same default as a literal 'PNG' and must stay in step with this value.
DEFAULT_OUTPUT_FORMAT = 'PNG'

# The ``render.image_settings.color_depth`` values Blender accepts for each
# OutputFormat (#243), as the strings its enum takes. Measured on official
# Blender 4.1, 4.2, 4.5, 5.0 and 5.2 builds; identical on all of them.
FORMAT_COLOR_DEPTHS = {
    'PNG': ('8', '16'),
    'JPEG': ('8',),
    'OPEN_EXR': ('16', '32'),
    'OPEN_EXR_MULTILAYER': ('16', '32'),
    'TIFF': ('8', '16'),
    'BMP': ('8',),
    'HDR': ('32',),
    'TARGA': ('8',),
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

# The worker agent's default heartbeat interval (``[worker] heartbeat_interval``).
WORKER_HEARTBEAT_INTERVAL_SECONDS = 30
# Live reconciliation (Unreal foundation FR-RT-17; Blender too since #247)
# leaves a RENDERING job alone until it started more than two heartbeat
# intervals ago.
LIVE_RECONCILE_GRACE_SECONDS = 2 * WORKER_HEARTBEAT_INTERVAL_SECONDS


class YieldReason(models.TextChoices):
    """Reasons a worker yielded a render job.

    The worker_* reasons (#246, #247) hand a job back on a deliberate stop
    through yield-requeue. A yield does not count against the auto-requeue
    limit, so ``worker_shutdown`` stays uncounted ("Interrupted:
    worker_shutdown"): stopping a machine often must not fail a healthy
    job. ``worker_hardware_changed`` is counted (shared
    ``auto_requeue_count``, ERROR above the limit, "Auto-requeued: worker
    hardware changed"): a scene that itself resets the GPU driver reads
    as a lost card, and uncounted it would be handed back for ever.
    ``blender_version_missing`` (the worker does not have the job's exact
    Blender version installed, so no render started) is counted the same
    way ("Auto-requeued: Blender version missing on worker"), so a job
    cannot bounce between workers for ever.
    Restart reconciliation stays the counted backstop for crashes.
    """
    ARTIST_RETURN_GPU_CONTENTION = (
        'artist_return_gpu_contention', 'Artist Return (GPU Contention)')
    ARTIST_RETURN_APP_LAUNCH = (
        'artist_return_app_launch', 'Artist Return (App Launch)')
    ARTIST_RETURN_SESSION_UNLOCK = (
        'artist_return_session_unlock', 'Artist Return (Session Unlock)')
    ARTIST_RETURN_SUSTAINED_ACTIVITY = (
        'artist_return_sustained_activity',
        'Artist Return (Sustained Activity)')
    MANUAL_OVERRIDE = (
        'manual_override', 'Manual Override')
    SCHEDULE_WINDOW_CLOSED = (
        'schedule_window_closed', 'Schedule Window Closed')
    WORKER_SHUTDOWN = (
        'worker_shutdown', 'Worker Shutdown')
    WORKER_HARDWARE_CHANGED = (
        'worker_hardware_changed', 'Worker Hardware Changed')
    BLENDER_VERSION_MISSING = (
        'blender_version_missing', 'Blender Version Missing')


class GraceOutcome(models.TextChoices):
    """Outcome of the grace period after a yield trigger."""
    FINISHED = 'finished', 'Finished'
    ABORTED = 'aborted', 'Aborted'
