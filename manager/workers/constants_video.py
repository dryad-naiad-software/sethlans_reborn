# SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Video assembly constants: assembly states, presets and the ffmpeg
codec and container allowlists.

``workers.constants`` re-exports every name here, so older imports
keep working.
"""
from django.db import models


class VideoStatus(models.TextChoices):
    """
    Defines the possible states of video assembly for an animation.
    """
    PENDING = 'PENDING', 'Pending'
    ASSEMBLING = 'ASSEMBLING', 'Assembling'
    DONE = 'DONE', 'Done'
    ERROR = 'ERROR', 'Error'


# Video assembly preset configurations mapping preset names to codec settings.
VIDEO_PRESETS = {
    'web_h264': {'container': 'mp4', 'codec': 'libx264', 'crf': 23},
    'hq_h265': {'container': 'mp4', 'codec': 'libx265', 'crf': 28},
    'web_vp9': {'container': 'webm', 'codec': 'libvpx-vp9', 'crf': 31},
    'archive_prores': {'container': 'mov', 'codec': 'prores_ks', 'crf': 0},
}

# Allowed video codecs for ffmpeg command construction.
VIDEO_CODECS = frozenset({'libx264', 'libx265', 'libvpx-vp9', 'prores_ks'})

# Allowed video container formats.
VIDEO_CONTAINERS = frozenset({'mp4', 'webm', 'mov'})

# Maps each codec to its valid container formats.
VIDEO_CODEC_CONTAINER_MAP = {
    'libx264': ['mp4'],
    'libx265': ['mp4'],
    'libvpx-vp9': ['webm'],
    'prores_ks': ['mov'],
}

# Maps container formats to their MIME content types.
CONTAINER_CONTENT_TYPES = {
    'mp4': 'video/mp4',
    'webm': 'video/webm',
    'mov': 'video/quicktime',
}

# Image formats compatible with video assembly (excludes HDR formats).
VIDEO_COMPATIBLE_FORMATS = frozenset({'PNG', 'JPEG', 'TIFF', 'BMP', 'TARGA'})

# The audio codec arguments of a merged video, by container (Unreal
# sequences, FR-SV-6): a fixed map, never a client value.
VIDEO_AUDIO_CODECS = {
    'mp4': ('aac', '-b:a', '192k'),
    'webm': ('libopus', '-b:a', '192k'),
    'mov': ('pcm_s16le',),
}

# The MP4 and MOV track timescale for a rate num/den (D13): the smallest
# multiple k x num of at least 90000, so every frame lasts exactly k x den
# ticks (24000/1001: 96000, 4004 ticks; 30000/1001: 90000, 3003; 60000/1001:
# 120000, 2002; 24: 90000, 3750).  Every frame then has the same duration
# (constant frame rate) and shot videos join by stream copy.  WebM has a
# fixed 1 ms timebase.
TRACK_TIMESCALE_BASE = 90000
TIMESCALE_CONTAINERS = frozenset({'mp4', 'mov'})
